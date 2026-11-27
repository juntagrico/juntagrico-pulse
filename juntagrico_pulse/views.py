import copy
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, F, FloatField, Q, Sum
from django.db.models.functions import Coalesce
from django.http.response import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from juntagrico.config import Config

from juntagrico.entity.subs import SubscriptionPart
from juntagrico.models import Member, Subscription
from juntagrico.util.models import q_isactive
from juntagrico.util.temporal import get_business_date_range

from .models import CHILD_WEIGHT, Survey, SurveyResult, SurveyResultMember
from .forms import SurveyForm, SurveyProductFormSet, SurveyResponseForm

DISTRIBUTION_CAP = 10


def results_asking(results, products):
    """Results whose subscription has an active part of a bundle of any of the products."""
    parts = SubscriptionPart.objects.filter(q_isactive(), type__bundle__survey_products__in=products)
    return results.filter(subscription__parts__in=parts).distinct()


@permission_required('juntagrico_pulse.view_survey')
def survey_overview(request):
    surveys = Survey.objects.annotate(
        selected_member_count=Count(
            'results__member_links__member',
            distinct=True,
        )
    ).order_by('-year')

    return render(
        request,
        'juntagrico_pulse/overview.html',
        {'surveys': surveys},
    )


@permission_required('juntagrico_pulse.change_survey')
@require_http_methods(['GET', 'POST'])
def start_survey(request):
    form = SurveyForm(request.POST or None, submit_text='Entwurf erstellen')

    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            survey = form.save()

            # collect all active subscriptions first
            for subscription in (
                Subscription.objects.active()
                .prefetch_related('members')
                .iterator(chunk_size=100)
            ):
                SurveyResult.objects.create_with_members(
                    survey=survey,
                    subscription=subscription,
                    members=subscription.current_members,
                )

            # collect future subscriptions and all their members that aren't in an active subscription already
            today = datetime.date.today()
            for waiting_subscription in (
                Subscription.objects.exclude(activation_date__lte=today)
                .prefetch_related('members')
                .iterator(chunk_size=100)
            ):
                SurveyResult.objects.create_with_members(
                    survey=survey,
                    subscription=waiting_subscription,
                    members=waiting_subscription.current_members.exclude(covered_survey_results__survey=survey),
                )

            # collect remaining active members without a subscription
            for member in (
                Member.objects.active()
                .exclude(covered_survey_results__survey=survey)
                .iterator()
            ):
                SurveyResult.objects.create_with_members(
                    survey=survey,
                    members=[member],
                )

        return redirect('juntagrico-pulse:review', survey_id=survey.pk)

    return render(request, 'juntagrico_pulse/start.html', {'form': form})


def get_draft(survey_id):
    survey = get_object_or_404(
        Survey,
        pk=survey_id,
    )
    if survey.status != Survey.Status.DRAFT:
        raise PermissionDenied('Nur Umfrage-Entwürfe können bearbeitet werden.')
    return survey


@permission_required('juntagrico_pulse.change_survey')
@require_http_methods(['GET', 'POST'])
def review_survey(request, survey_id):
    survey = get_draft(survey_id)

    data = request.POST or None
    # a copy, so that rejected input doesn't show up elsewhere on the page, e.g. in the heading
    form = SurveyForm(data, instance=copy.copy(survey), form_tag=False)
    product_formset = SurveyProductFormSet(data, instance=survey, prefix='products')
    # validate both, so that the errors of both are shown at once
    valid = [form.is_valid(), product_formset.is_valid()]
    if request.method == 'POST' and all(valid):
        with transaction.atomic():
            survey = form.save()
            product_formset.save()
        messages.success(request, 'Änderungen gespeichert!')
        return redirect('juntagrico-pulse:review', survey_id=survey.pk)

    results = (
        SurveyResult.objects.filter(survey=survey)
        .select_related('subscription')
        .prefetch_related('members')
        .order_by('subscription_id', 'id')
    )

    excluded = Member.objects.active().exclude(covered_survey_results__survey=survey)

    # number of results each saved product is asked in, to check the configuration before publishing
    for product_form in product_formset:
        product = product_form.instance
        if product.pk:
            product_form.result_count = results_asking(results, [product]).count()

    return render(
        request,
        'juntagrico_pulse/review.html',
        {
            'survey': survey,
            'form': form,
            'product_formset': product_formset,
            'results': results,
            'excluded': excluded,
        },
    )


@permission_required('juntagrico_pulse.change_survey')
@require_POST
def add_survey_member(request, survey_id, member_id):
    survey = get_draft(survey_id)

    with transaction.atomic():
        member = get_object_or_404(
            Member.objects.select_for_update(),
            pk=member_id,
        )
        if member.inactive:
            raise Http404()

        current_subscription = member.subscription_current
        if current_subscription is not None:
            result, _ = SurveyResult.objects.get_or_create(survey=survey, subscription=member.subscription_current)
        else:
            result = SurveyResult.objects.create(survey=survey)
        SurveyResultMember.objects.update_or_create(result=result, member=member)

    return redirect('juntagrico-pulse:review', survey_id=survey.pk)


@permission_required('juntagrico_pulse.change_survey')
@require_POST
def remove_survey_member(request, survey_id, result_id, member_id):
    survey = get_draft(survey_id)

    with transaction.atomic():
        result = get_object_or_404(
            SurveyResult.objects.select_for_update(),
            pk=result_id,
            survey=survey,
        )
        member_link = get_object_or_404(
            SurveyResultMember,
            result=result,
            member_id=member_id,
        )
        member_link.delete()

        if not result.member_links.exists():
            result.delete()

    return redirect('juntagrico-pulse:review', survey_id=survey.pk)


@permission_required('juntagrico_pulse.change_survey')
@require_POST
def publish_survey(request, survey_id):
    with transaction.atomic():
        survey = get_object_or_404(
            Survey,
            pk=survey_id,
        )

        # Prevent publishing an empty survey.
        if not SurveyResultMember.objects.filter(result__survey=survey).exists():
            messages.error(
                request,
                f'Eine Umfrage muss mindestens ein ausgewähltes {Config.vocabulary("member")} haben.',
            )
            return redirect('juntagrico-pulse:review', survey_id=survey.pk)

        survey.status = Survey.Status.PUBLISHED
        survey.save(update_fields=['status'])

        if not results_asking(survey.results.all(), survey.products.all()).exists():
            messages.warning(
                request,
                'Es werden keine Fragen zum Versorgungsgrad gestellt, '
                'weil kein Produkt zu einem aktiven Bestandteil passt.',
            )

    return redirect('juntagrico-pulse:results', survey_id=survey.pk)


@permission_required('juntagrico_pulse.change_survey')
@require_POST
def draft_survey(request, survey_id):
    with transaction.atomic():
        survey = get_object_or_404(
            Survey.objects.select_for_update(),
            pk=survey_id,
            status=Survey.Status.PUBLISHED,
        )
        survey.status = Survey.Status.DRAFT
        survey.save(update_fields=["status"])

    return redirect("juntagrico-pulse:review", survey_id=survey.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def fill_survey(request, survey_id):
    member = request.user.member
    next_url = request.GET.get('next', '')

    result = get_object_or_404(
        SurveyResult.objects.select_related('survey'),
        survey_id=survey_id,
        survey__status=Survey.Status.PUBLISHED,
        member_links__member=member,
    )

    business_year_start, business_year_end = get_business_date_range(result.survey.year)
    today = datetime.date.today()
    season_ended = business_year_end < today
    members = list(result.members.order_by('first_name', 'last_name'))
    plural = len(members) > 1
    products = list(result.products())
    form_kwargs = {'products': products, 'season_ended': season_ended, 'plural': plural}

    if request.method == 'POST':
        with transaction.atomic():
            result = get_object_or_404(
                SurveyResult.objects.select_for_update().select_related('survey'),
                pk=result.pk,
                survey__status=Survey.Status.PUBLISHED,
                member_links__member=member,
            )

            form = SurveyResponseForm(request.POST, instance=result, **form_kwargs)
            if form.is_valid():
                form.instance.submitted_at = timezone.now()
                form.instance.submitted_by = member
                form.save()

                if url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)

                return redirect('home')
    else:
        form = SurveyResponseForm(instance=result, **form_kwargs)

    return render(
        request,
        'juntagrico_pulse/fill.html',
        {
            'survey': result.survey,
            'result': result,
            'form': form,
            'products': products,
            'business_year_start': business_year_start,
            'business_year_end': business_year_end,
            'season_started': business_year_start <= today,
            'season_ended': season_ended,
            'members': members,
            'plural': plural,
            'already_submitted': result.submitted_at is not None,
        },
    )


@permission_required('juntagrico_pulse.view_survey')
def survey_results(request, survey_id):
    survey = get_object_or_404(
        Survey,
        pk=survey_id,
    )

    results = SurveyResult.objects.filter(survey=survey)
    completed = results.filter(submitted_at__isnull=False)

    stats = results.aggregate(
        completed_subscriptions=Count(
            "pk",
            filter=Q(
                subscription__isnull=False,
                submitted_at__isnull=False,
            ),
        ),
        pending_subscriptions=Count(
            "pk",
            filter=Q(
                subscription__isnull=False,
                submitted_at__isnull=True,
            ),
        ),
        completed_members=Count(
            "pk",
            filter=Q(
                subscription__isnull=True,
                submitted_at__isnull=False,
            ),
        ),
        pending_members=Count(
            "pk",
            filter=Q(
                subscription__isnull=True,
                submitted_at__isnull=True,
            ),
        ),
    )

    stats['completed'] = stats['completed_subscriptions'] + stats['completed_members']
    stats['pending'] = stats['pending_subscriptions'] + stats['pending_members']

    participation_summary = completed.aggregate(
        total=Sum("participation_level"),
        average=Avg("participation_level"),
    )

    people_summary = completed.aggregate(
        adults=Sum('people_eating_product'),
        children=Sum('children_eating_product'),
    )

    # per product: people eating it and the coverage, weighted by people (children count CHILD_WEIGHT)
    submitted = Q(coverages__result__submitted_at__isnull=False)
    # an admin may have left a count empty
    adults = Coalesce(F('coverages__result__people_eating_product'), 0)
    children = Coalesce(F('coverages__result__children_eating_product'), 0)
    equivalent = adults + children * CHILD_WEIGHT
    products = list(
        survey.products.annotate(
            people=Sum(adults + children, filter=submitted, default=0),
            children=Sum(children, filter=submitted, default=0),
            equivalent_people=Sum(equivalent, filter=submitted, default=0, output_field=FloatField()),
            fed_equivalent=Sum(
                equivalent * F('coverages__percent') / 100.0,
                filter=submitted,
                default=0,
                output_field=FloatField(),
            ),
        )
    )
    for product in products:
        product.average_percent = (
            product.fed_equivalent / product.equivalent_people * 100 if product.equivalent_people else None
        )

    counts = {
        row['participation_level']: row['count']
        for row in completed.exclude(participation_level=None)
        .values('participation_level')
        .annotate(count=Count('id'))
    }

    # one bar per level, with large levels grouped into a single last bar
    distribution = []
    if counts:
        distribution = [
            {
                'participation_level': value,
                'count': counts.get(value, 0),
            }
            for value in range(min(max(counts), DISTRIBUTION_CAP - 1) + 1)
        ]
        over_cap = sum(count for value, count in counts.items() if value >= DISTRIBUTION_CAP)
        if over_cap:
            distribution.append({'participation_level': f'{DISTRIBUTION_CAP}+', 'count': over_cap})

    return render(
        request,
        'juntagrico_pulse/results.html',
        {
            'survey': survey,
            'stats': stats,
            'participation_summary': participation_summary,
            'people_summary': people_summary,
            'products': products,
            'child_weight': CHILD_WEIGHT,
            'distribution': distribution,
        },
    )


@permission_required('juntagrico_pulse.view_survey')
def survey_individual_results(request, survey_id):
    survey = get_object_or_404(
        Survey,
        pk=survey_id,
    )

    results = (
        SurveyResult.objects.filter(survey=survey)
        .select_related('subscription', 'submitted_by')
        .prefetch_related('members', 'product_coverages__product')
    )

    return render(request, "juntagrico_pulse/individual_results.html", {
        "survey": survey,
        "results": results,
    })


@permission_required('juntagrico_pulse.change_survey')
@require_POST
def close_survey(request, survey_id):
    with transaction.atomic():
        survey = get_object_or_404(
            Survey.objects.select_for_update(),
            pk=survey_id,
            status=Survey.Status.PUBLISHED,
        )
        survey.status = Survey.Status.CLOSED
        survey.save(update_fields=["status"])

    return redirect("juntagrico-pulse:results", survey_id=survey.pk)
