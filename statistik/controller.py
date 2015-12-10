"""
Helper methods with which views.py can interact with models.py
"""
import random
import statistics

import elo
from django.contrib.auth.models import User
from django.db import transaction
from statistik.constants import (SCORE_CATEGORY_NAMES, TECHNIQUE_CHOICES,
                                 RECOMMENDED_OPTIONS_CHOICES)
from statistik.forms import ReviewForm
from statistik.models import Chart, Review, UserProfile, EloReview


def organize_reviews(matched_reviews, user_id):
    """
    Helper method for get_avg_ratings.
    :param Queryset matched_reviews:    Queryset(or iterable) of Review objects

    :param int user_id:                 User id to identify which charts that
                                        user has rated

    :rtype tuple:                       Tuple containing a dict mapping chart
                                        ids to lists of their reviews, as well
                                        as a set of the ids of charts that have
                                        been reviewed by this user
    """
    review_dict = {}
    user_reviewed = set([review.chart_id for review in matched_reviews
                         if review.user_id == user_id])
    for review in matched_reviews:
        if review.chart_id in review_dict:
            review_dict[review.chart_id].append(review)
        else:
            review_dict[review.chart_id] = [review]
    return review_dict, user_reviewed


def get_avg_ratings(chart_ids, user_id=None):
    """
    Get average ratings for all charts.
    :param list chart_ids:  List of chart ids to retrieve ratings for

    :param int user_id:     User id to identify which charts that user has
                            rated

    :rtype dict:            Dict mapping chart ids to a dict of the average
                            ratings for that chart as well as a has_reviewed
                            boolean.
    """
    matched_reviews = Review.objects.filter(chart__in=chart_ids)
    organized_reviews, reviewed_charts = organize_reviews(matched_reviews,
                                                          user_id=user_id)
    ret = {}

    for chart in chart_ids:
        ret[chart] = {}
        # get reviews for this chart
        specific_reviews = organized_reviews.get(chart)
        if specific_reviews:
            # for each rating type, average the scores in matched reviews
            for rating_type in SCORE_CATEGORY_NAMES:
                avg_rating = "%.1f" % round(statistics.mean(
                    [getattr(review, rating_type)
                     for review in specific_reviews
                     if getattr(review, rating_type) is not None] or [0]), 1)

                # if average is '0.0', normalize that to 0
                if avg_rating != "0.0":
                    ret[chart][rating_type] = avg_rating
                else:
                    ret[chart][rating_type] = 0

            # set 'has_reviewed' if the user has reviewed this chart
            ret[chart]['has_reviewed'] = (chart in reviewed_charts)

        # winding up here means no reviews were found for one of the charts
        # which means the template will just use '--' for all the ratings
        else:
            ret[chart] = {}

    return ret


def get_charts_by_ids(ids):
    """
    Chart lookup by id
    :param list ids: List of int ids
    :rtype Queryset: Queryset containing matched charts
    """
    return Chart.objects.filter(pk__in=ids)


def get_charts_by_query(version=None, difficulty=None, play_style='SP'):
    """
    Chart lookup by game-related parameters
    :param version:         Game version to filter by (from VERSION_CHOICES)
    :param difficulty:      Difficulty to filter by (1-12)
    :param str play_style:  Play style to filter by (from PLAYSIDE_CHOICES)
    :rtype Queryset: Queryset of matched Chart objects
    """

    filters = {}
    # create filters for songlist based off params
    if version:
        filters['song__game_version'] = int(version)
    if difficulty:
        filters['difficulty'] = int(difficulty)
    if not (version or difficulty):
        filters['difficulty'] = 12

    filters['type__in'] = {
        'SP': [0, 1, 2],
        'DP': [3, 4, 5]
    }[play_style]

    return Chart.objects.filter(**filters).prefetch_related('song').order_by(
        'song__game_version')


def get_chart_data(version=None, difficulty=None, play_style=None, user=None):
    """
    Retrieve chart data acc to specified params and format chart data for
    usage in templates.
    :param int version:     Game version to filter by (from VERSION_CHOICES)
    :param int difficulty:  Difficulty to filter by (1-12)
    :param str play_style:  Play style to filter by (from PLAYSIDE_CHOICES)
    :param int user:        Mark charts that have been rated by this user
    :rtype list:            List of dicts containing chart data
    """
    matched_charts = get_charts_by_query(version, difficulty, play_style)
    matched_chart_ids = [chart.id for chart in matched_charts]

    # get avg ratings for the charts in the returned queryset
    avg_ratings = get_avg_ratings(matched_chart_ids, user)

    chart_data = []
    for chart in matched_charts:
        chart_data.append({
            'id': chart.id,

            'title': chart.song.title,
            'alt_title': chart.song.alt_title
            if chart.song.alt_title
            else chart.song.title,

            'note_count': chart.note_count,
            'difficulty': chart.difficulty,

            'avg_clear_rating': avg_ratings[chart.id].get(
                'clear_rating'),
            'avg_hc_rating': avg_ratings[chart.id].get(
                'hc_rating'),
            'avg_exhc_rating': avg_ratings[chart.id].get(
                'exhc_rating'),
            'avg_score_rating': avg_ratings[chart.id].get(
                'score_rating'),

            'game_version': chart.song.game_version,
            'game_version_display': chart.song.get_game_version_display(),
            'type_display': chart.get_type_display(),

            'has_reviewed': avg_ratings[chart.id].get(
                'has_reviewed'
            )
        })
    return chart_data


def generate_review_form(user, chart_id, form_data=None):
    """
    Generate ReviewForm as necessary for a user/chart combo
    :param User user:       User as found in request.user
    :param int chart_id:    ID of chart to lookup
    :param dict form_data:  Form data to process as a POST request
    :rtype ReviewForm:      ReviewForm with fields pre-populated as necessary
    """
    chart = Chart.objects.get(pk=chart_id)

    # if user is authenticated and can review this chart, display review form
    if user.is_authenticated():
        user_profile = UserProfile.objects.filter(user=user).first()
        if user_profile and user_profile.max_reviewable >= chart.difficulty:

            # handle incoming reviews
            if form_data:
                form = ReviewForm(form_data)
                if form.is_valid(difficulty=chart.difficulty):
                    Review.objects.update_or_create(chart=chart,
                                                    user=user,
                                                    defaults=form.cleaned_data)
            # handle regular page requests
            else:
                # check if user has an existing review for this chart
                user_review = Review.objects.filter(
                    user=user, chart=chart).first()
                # if they do, pre-populate the form fields with this review
                if user_review:
                    data = {key: getattr(user_review, key)
                            for key in ['text',
                                        'clear_rating',
                                        'hc_rating',
                                        'exhc_rating',
                                        'score_rating',
                                        'characteristics',
                                        'recommended_options']}
                    form = ReviewForm(data)

                # if they don't, create a blank form
                else:
                    form = ReviewForm()
            return form


def get_reviews_for_chart(chart_id):
    """
    Get reviews for a chart and format for usage in template
    :param int chart_id:    ID of chart to get reviews for
    :rtype list:            List of dicts with review info
    """
    chart_reviews = Review.objects.filter(chart=chart_id).prefetch_related(
        'user__userprofile')

    # collect info to display for each review
    review_data = []
    for review in chart_reviews:
        review_data.append({
            'user': review.user.get_username(),
            'user_id': review.user.id,
            'playside': review.user.userprofile.get_play_side_display(),

            'text': review.text,
            'clear_rating': review.clear_rating,
            'hc_rating': review.hc_rating,
            'exhc_rating': review.exhc_rating,
            'score_rating': review.score_rating,

            'characteristics': ', '.join([
                TECHNIQUE_CHOICES[x][1]
                for x in review.characteristics]),

            'recommended_options': ', '.join([
                RECOMMENDED_OPTIONS_CHOICES[x][1]
                for x in review.recommended_options])
        })
    return review_data


def get_reviews_for_user(user_id):
    """
    Get reviews written by a user and format for use in template
    :param int user_id:     User to query reviews by
    :rtype list:            List of dicts containing user's reviews
    """
    # get all reviews created by this user
    matched_reviews = Review.objects.filter(user=user_id).prefetch_related(
        'chart__song')
    # assemble display info for these reviews
    review_data = []
    for review in matched_reviews:
        review_data.append({
            'title': review.chart.song.title,
            'text': review.text,
            'chart_id': review.chart.id,
            'type_display': review.chart.get_type_display(),
            'difficulty': review.chart.difficulty,

            'clear_rating': review.clear_rating,
            'hc_rating': review.hc_rating,
            'exhc_rating': review.exhc_rating,
            'score_rating': review.score_rating,

            'characteristics': ', '.join([
                TECHNIQUE_CHOICES[x][1] for x in
                review.characteristics]),
            'recommended_options': ', '.join([
                RECOMMENDED_OPTIONS_CHOICES[x][1]
                for x in review.recommended_options])
        })

    return review_data


def get_user_list():
    """
    Get data for all registered users and format for template usage
    :rtype list:    List of dicts containing user info
    """
    users = User.objects.filter(is_superuser=False).prefetch_related(
        'userprofile')

    # assemble display info for users
    user_data = []
    for user in users:
        user_data.append({
            'user_id': user.id,
            'username': user.username,
            'dj_name': user.userprofile.dj_name,

            'playside': user.userprofile.get_play_side_display(),
            'best_techniques': ', '.join([
                TECHNIQUE_CHOICES[x][1]
                for x in user.userprofile.best_techniques]),

            'max_reviewable': user.userprofile.max_reviewable,
            'location': user.userprofile.location
        })
    return user_data


def create_new_user(user_data):
    """
    Create new User/UserProfle combo
    :param dict user_data:  Data with which to create user
    """
    user = User.objects.create_user(username=user_data.get('username'),
                                    password=user_data.get('password'),
                                    email=user_data.get('email'))
    user.save()
    user_profile = UserProfile(user_id=user.id,
                               dj_name=user_data.get('dj_name').upper(),
                               location=user_data.get('location'),
                               play_side=user_data.get('playside'),
                               best_techniques=user_data.get(
                                   'best_techniques'),
                               max_reviewable=0)
    user_profile.save()
    return user


def elo_rate_charts(chart1_id, chart2_id, draw=False, rate_type=0):
    """
    Add new Elo rating for two charts
    :param int chart1_id:   ID of winning chart
    :param int chart2_id:   ID of losing chart
    :param bool draw:       True if match was a draw
    :param int rate_type:   Rating type (refer to Chart model for options)
    """
    rate_type_display = 'elo_rating_hc' if rate_type else 'elo_rating'
    with transaction.atomic():
            win_chart = Chart.objects.get(pk=chart1_id)
            lose_chart = Chart.objects.get(pk=chart2_id)

            # elo magic happens here
            elo_env = elo.Elo(k_factor=20)
            win_chart_elo = getattr(win_chart, rate_type_display)
            lose_chart_elo = getattr(lose_chart, rate_type_display)
            win_rating, lose_rating = elo_env.rate_1vs1(win_chart_elo,
                                                        lose_chart_elo,
                                                        drawn=draw)

            # update charts with new elo ratings
            setattr(win_chart, rate_type_display, win_rating)
            setattr(lose_chart, rate_type_display, lose_rating)
            win_chart.save()
            lose_chart.save()

            # record review in case ratings need to be regenerated
            EloReview.objects.create(first=win_chart,
                                     second=lose_chart,
                                     drawn=draw,
                                     type=rate_type)


def get_elo_rankings(level, rate_type):
    """
    Get songs ranked by Elo ranking, formatted for template usage
    :param int level:       Level to sort by (1-12)
    :param str rate_type:   Rating type (refer to Chart model for options)
    :rtype list:            List of dicts containing chart/ranking data
    """
    matched_charts = Chart.objects.filter(difficulty=int(level), type__lt=3).prefetch_related('song').order_by('-' + rate_type)

    # assemble displayed elo info for matched charts
    # TODO add link to 'normal' chart reviews
    chart_data = []
    for rank, chart in enumerate(matched_charts):
        chart_data.append({
            'index': rank + 1,
            'id': chart.id,
            'title': chart.song.title,
            'type': chart.get_type_display(),
            'rating': round(getattr(chart, rate_type), 3)
        })
    return chart_data


def make_elo_matchup(level):
    """
    Match two charts for an Elo ranking and format the data for template usage
    :param int level:   Level of songs to match (1-12)
    :rtype list:        List of dicts of chart info
    """
    elo_diff = 9001
    chart1 = chart2 = None
    charts = list(Chart.objects.filter(difficulty=int(level), type__lt=3))

    # only return closely-matched charts for better rankings
    while elo_diff > 50:
        [chart1, chart2] = random.sample(charts, 2)
        elo_diff = abs(chart1.elo_rating-chart2.elo_rating)

    # assemble display info for these two charts
    return [{
        'title': chart.song.title,
        'type': chart.get_type_display(),
        'id': chart.id
    } for chart in [chart1, chart2]]


def add_page_titles(context, title_elements, page_link=None):
    """

    :param dict context:
    :param listtitle_elements:
    :param str page_link:
    :return:
    """

    context['title'] = ' // '.join(title_elements)
    context['page_title'] = ' // '.join(['STATISTIK', context['title']])
    if page_link:
        context['page_link'] = page_link