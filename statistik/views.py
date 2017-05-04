"""
Main view controller for Statistik
"""
import json

from django.contrib.auth import logout, authenticate, login
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db import IntegrityError
from django.http import (HttpResponseBadRequest, HttpResponseRedirect,
                         HttpResponse)
from django.shortcuts import redirect, render
from django.utils.translation import ugettext as _
from statistik.constants import (FULL_VERSION_NAMES, generate_version_urls,
                                 generate_level_urls, SCORE_CATEGORY_CHOICES,
                                 generate_elo_level_urls, IIDX, DDR, GAMES)
from statistik.controller import (get_chart_data, generate_review_form,
                                  get_charts_by_ids, get_reviews_for_chart,
                                  get_reviews_for_user, get_user_list,
                                  create_new_user, elo_rate_charts,
                                  get_elo_rankings, make_elo_matchup,
                                  create_page_title, make_nav_links,
                                  generate_user_form, delete_review, make_game_links)
from statistik.forms import RegisterForm, SearchForm


def index(request):
    """
    Returns index page
    :param request: Request to handle
    :rtype HTTPResponse:
    """

    game = int(request.GET.get('game', IIDX))
    context = {
        'index_links': [
            (_('STANDARD RATINGS'), reverse('ratings')),
            (_('ELO RATINGS'), reverse('elo')),
            (_('USER LIST'), reverse('users')),
            (_('SEARCH'), reverse('search'))
        ],

        'title': 'STATISTIK // ' + _('INDEX') + ' // ' + GAMES[game],
        'page_title': 'STATISTIK // ' + _('INDEX') + ' // ' + GAMES[game],
        'game': game,
        'game_links': make_game_links(game)
    }
    return render(request, 'index.html', context)


def ratings_view(request):
    """
    Assemble ratings page. Possible filters include difficulty and version.
    :rtype dict: Context including chart data
    """
    # TODO: see if game can be removed from the url params
    game = int(request.GET.get('game', IIDX))
    difficulty = request.GET.get('difficulty')
    versions = request.GET.getlist('version')
    play_style = request.GET.get('style', 'SP')
    user = request.user.id

    if versions:
        game = int(versions[0]) // 100
    # TODO: have the nav link for all songs of that level show songs from the right game

    # remove any None keys to avoid having to check for them later
    params = {k: v for k, v in {
        'min_difficulty': request.GET.get('min_difficulty'),
        'max_difficulty': request.GET.get('max_difficulty'),
        'title': request.GET.get('title'),
        'genre': request.GET.get('genre'),
        'artist': request.GET.get('artist'),
        'play_style': request.GET.getlist('play_style'),
        'min_nc': request.GET.get('min_nc'),
        'max_nc': request.GET.get('max_nc'),
        'min_hc': request.GET.get('min_hc'),
        'max_hc': request.GET.get('max_hc'),
        'min_exhc': request.GET.get('min_exhc'),
        'max_exhc': request.GET.get('max_exhc'),
        'min_score': request.GET.get('min_score'),
        'max_score': request.GET.get('max_score'),
        'techs': request.GET.getlist('techs')
    }.items() if v}

    # if not a search and nothing was specified, show 12a
    if not request.GET.get('submit') and not (difficulty or versions):
        difficulty = 12

    chart_data = get_chart_data(game, versions, difficulty, play_style, user, params,
                                include_reviews=bool(request.GET.get('json')))

    if request.GET.get('json') == 'true':
        return HttpResponse(
            json.dumps({'data': chart_data}, indent=4, ensure_ascii=False))

    # assemble displayed info for each of the charts
    context = {
        'charts': chart_data
    }

    # assemble page title
    title_elements = []
    if request.GET.get('submit'):
        title_elements.append('SEARCH RESULTS')
    else:
        title_elements.insert(0, {0: 'IIDX', 1: 'DDR'}[game])
        if versions:
            title_elements.append(FULL_VERSION_NAMES[game][int(versions[0])].upper())
        if difficulty or not (difficulty or versions[0]):
            title_elements.append('LV. ' + str(difficulty or 12))
        title_elements.append(play_style)
    create_page_title(context, title_elements)

    # create version/level navigator to display above songlist
    context['versions'] = generate_version_urls(game)
    context['levels'] = generate_level_urls(game)

    context['nav_links'] = make_nav_links(game=game)

    if game == IIDX:
        return render(request, 'ratings_iidx.html', context)
    else:
        return render(request, 'ratings_ddr.html', context)


def chart_view(request):
    """
    Handle requests for individual chart pages (mostly collections of reviews)
    :param request: Request to handle
    """
    context = {}

    chart_id = request.GET.get('id')
    if not chart_id:
        return HttpResponseBadRequest()

    if request.GET.get('delete') == 'true' and request.user.is_authenticated():
        delete_review(request.user.id, chart_id)
        return HttpResponseRedirect(reverse('chart') + '?id=%s' % chart_id)

    # TODO remove all direct interaction with chart
    chart = get_charts_by_ids([chart_id])[0]

    # truncate long song title
    song_title = chart.song.title if len(
            chart.song.title) <= 15 else chart.song.title[:15] + '...'

    # assemble page title
    title_elements = [song_title,
                      chart.get_type_display(),
                      str(chart.difficulty) + '☆']
    create_page_title(context, title_elements)

    context['difficulty'] = chart.difficulty
    context['chart_id'] = chart_id

    game = chart.song.game_version // 100

    form_data = request.POST if request.method == 'POST' else None
    context['form'], context['review_exists'] = generate_review_form(
            request.user, chart_id, form_data)

    # get reviews for this chart, cache users for username and playside lookup
    context['reviews'] = get_reviews_for_chart(chart_id)
    if game == IIDX:
        style = chart.get_type_display()[:2]
    else:
        style = chart.get_type_display()[1:]
        if style[0] == 'E':
            style = 'SP'

    context['nav_links'] = make_nav_links(game=game,
                                          level=chart.difficulty,
                                          style=style,
                                          version=chart.song.game_version)
    if game == IIDX:
        return render(request, 'chart_iidx.html', context)
    else:
        return render(request, 'chart_ddr.html', context)


def elo_view(request):
    """
    Handle requests for Elo views (lists as well as individual matchups)
    :param request: Request to handle
    """

    win = request.GET.get('win')
    lose = request.GET.get('lose')
    level = request.GET.get('level', '12')
    display_list = bool(request.GET.get('list'))
    clear_type = int(request.GET.get('type', 0))
    game = int(request.GET.get('game', IIDX))

    if not (display_list or request.user.is_authenticated()):
        return HttpResponseRedirect(
                reverse('elo') + '?level=%s&type=%d&list=true' %
                (level, clear_type))

    # TODO extend to accommodate exhc and score types
    rate_type_column = 'elo_rating_hc' if clear_type == 1 else 'elo_rating'
    type_display = SCORE_CATEGORY_CHOICES[game][clear_type][1]

    # handle incoming elo reviews
    # TODO don't use GET for this
    if win and lose:
        draw = bool(request.GET.get('draw'))
        elo_rate_charts(int(win), int(lose), request.user, draw, clear_type)
        return HttpResponseRedirect(reverse('elo') + '?level=%s&type=%d' %
                                    (level, clear_type))

    # handle regular requests
    else:
        context = {}
        if display_list:
            # display list of charts ranked by elo
            # TODO fix line length
            context['chart_list'] = get_elo_rankings(game, level, rate_type_column)
            title_elements = ['ELO', GAMES[game] + ' ' + level + '☆ ' + type_display + _(' LIST')]
        else:
            # display two songs to rank
            [context['chart1'], context['chart2']] = make_elo_matchup(game, level)

            # add page title
            title_elements = ['ELO', GAMES[game] + ' ' + level + '☆ ' + type_display + _(' MATCHING')]

    create_page_title(context, title_elements)
    context['game'] = game
    context['level'] = level
    context['is_hc'] = clear_type
    context['is_hc_display'] = type_display
    context['level_links'] = generate_elo_level_urls(game)
    context['nav_links'] = make_nav_links(
            level=int(level),
            elo='list' if display_list else 'match',
            clear_type=clear_type,
            game=game)
    return render(request, 'elo_rating.html', context)


def user_view(request):
    """
    Handle requests for both individual user pages as well as the userlist
    :param request: Request to handle
    """
    context = {}
    user_id = request.GET.get('id')
    if user_id:
        # make sure this user actually exists
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return HttpResponseBadRequest()

        context['reviews'] = get_reviews_for_user(user.id)

        # if user is the logged in user, let them edit their options
        if request.user.id == int(user_id):
            context['form'] = generate_user_form(request.user, request.POST)

        # assemble page title
        title_elements = [user.username.upper(), _('REVIEWS')]
        create_page_title(context, title_elements)

        context['nav_links'] = make_nav_links(user=True)

        return render(request, 'user.html', context)

    else:
        # return list of all registered users
        context['users'] = get_user_list()

        # assemble page title
        title_elements = [_('USER LIST')]
        create_page_title(context, title_elements)
        context['nav_links'] = make_nav_links()

        return render(request, 'userlist.html', context)


def register_view(request):
    """
    Handle user registration
    :param request: Request to handle
    """
    context = {}
    if request.method == 'POST':
        # handle submitted registration forms
        # TODO refactor RegisterForm creation to somewhere else
        form = RegisterForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            # TODO verify user was actually created
            try:
                create_new_user(data)
            except IntegrityError:
                form.add_error('username', 'Username taken.')
            else:
                user = authenticate(username=data.get('username'),
                                    password=data.get('password'))
                login(request, user)
                return redirect('index')
    else:
        # display blank form
        form = RegisterForm()
    context['form'] = form

    # assemble page title
    title_elements = [_('REGISTRATION')]
    create_page_title(context, title_elements)

    return render(request, 'register.html', context)


def login_view(request):
    """
    POST only, handles user login
    :param request: Request to handle
    """
    # TODO make this a proper form to display errors and stuff
    username = request.POST.get('username')
    password = request.POST.get('password')
    user = authenticate(username=username, password=password)

    if user is not None:
        if user.is_active:
            login(request, user)

    return redirect('index')


def logout_view(request):
    """
    Logs out current user and redirects to index page
    :param request: Request to handle
    """
    logout(request)
    return redirect('index')


def search_view(request):
    """
    GET only, gives the user a search form
    :param request: Request to handle
    """
    context = {}
    title_elements = ['SEARCH']
    create_page_title(context, title_elements)
    if 'submit' in request.GET:
        form = SearchForm(request.GET)
        if form.is_valid():
            # pass on the search filters to the ratings view
            response = redirect('ratings')
            # delete any filters left empty or at the default to clean up the URL
            query = request.GET.copy()
            empty = []
            # can't delete while iterating over the query so mark them
            for key in query:
                if key != 'submit':
                    if not query[key] or query[key] == str(form.fields[key].initial):
                        empty.append(key)
            for key in empty:
                del (query[key])
            response['Location'] += '?' + query.urlencode()
            return response
    else:
        form = SearchForm()
    context['form'] = form
    context['nav_links'] = make_nav_links()
    return render(request, 'search.html', context)
