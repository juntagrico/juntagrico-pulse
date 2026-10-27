from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Sum, Q
from django.http.response import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from juntagrico.config import Config

from juntagrico.models import Member, Subscription

from .models import Survey, SurveyResult, SurveyResultMember
from .forms import StartSurveyForm, SurveyResponseForm


# TODO: protect admin pages with a permission
@login_required
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


@login_required
@require_http_methods(['GET', 'POST'])
def start_survey(request):
    form = StartSurveyForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        year = form.cleaned_data['year']

        with transaction.atomic():
            survey = Survey.objects.create(
                year=year,
                status=Survey.Status.DRAFT,
            )

            # TODO: should future subscriptions be included somehow?
            for subscription in (
                Subscription.objects.active()
                .prefetch_related('members')
                .iterator(chunk_size=100)
            ):
                members = subscription.current_members
                if not members:
                    continue

                result = SurveyResult.objects.create(
                    survey=survey,
                    subscription=subscription,
                )
                SurveyResultMember.objects.bulk_create(
                    [
                        SurveyResultMember(result=result, member=member)
                        for member in members
                    ]
                )

            # include remaining active members without a subscription
            for member in (
                Member.objects.active()
                .exclude(covered_survey_results__survey=survey)
                .iterator()
            ):
                result = SurveyResult.objects.create(survey=survey)
                SurveyResultMember.objects.create(result=result, member=member)

        return redirect('juntagrico-pulse:review', survey_id=survey.pk)

    return render(request, 'juntagrico_pulse/start.html', {'form': form})


def get_draft(survey_id):
    survey = get_object_or_404(
        Survey,
        pk=survey_id,
    )
    if survey.status != Survey.Status.DRAFT:
        # TODO: Allow editing published subscription
        raise PermissionDenied('Nur Umfrage-Entwürfe können bearbeitet werden.')
    return survey


@login_required
@require_http_methods(['GET'])
def review_survey(request, survey_id):
    survey = get_draft(survey_id)

    results = (
        SurveyResult.objects.filter(survey=survey)
        .select_related('subscription')
        .prefetch_related('members')
        .order_by('subscription_id', 'id')
    )

    excluded = Member.objects.active().exclude(covered_survey_results__survey=survey)

    return render(
        request,
        'juntagrico_pulse/review.html',
        {'survey': survey, 'results': results, 'excluded': excluded},
    )


@login_required
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

@login_required
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


@login_required
@require_POST
def publish_survey(request, survey_id):
    with transaction.atomic():
        survey = get_draft(survey_id)

        # Prevent publishing an empty survey.
        if not SurveyResultMember.objects.filter(result__survey=survey).exists():
            messages.error(
                request,
                f'Eine Umfrage muss mindestens ein ausgewähltes {Config.vocabulary("member")} haben.',
            )
            return redirect('juntagrico-pulse:review', survey_id=survey.pk)

        survey.status = Survey.Status.PUBLISHED
        survey.save(update_fields=['status'])

    return redirect('juntagrico-pulse:overview', survey_id=survey.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def fill_survey(request, survey_id):
    member = request.user.member
    next_url = request.POST.get('next') or request.GET.get('next', '')

    result = get_object_or_404(
        SurveyResult.objects.select_related('survey'),
        survey_id=survey_id,
        survey__status=Survey.Status.PUBLISHED,
        member_links__member=member,
    )

    if request.method == 'POST':
        with transaction.atomic():
            result = get_object_or_404(
                SurveyResult.objects.select_for_update().select_related('survey'),
                pk=result.pk,
                survey__status=Survey.Status.PUBLISHED,
                member_links__member=member,
            )

            form = SurveyResponseForm(request.POST, instance=result, next_url=next_url)
            if form.is_valid():
                result = form.save(commit=False)
                result.submitted_at = timezone.now()
                result.submitted_by = member
                result.save()

                if url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)

                return redirect('juntagrico-pulse:fill', survey_id=survey_id)
    else:
        form = SurveyResponseForm(instance=result, next_url=next_url)

    return render(
        request,
        'juntagrico_pulse/fill.html',
        {
            'survey': result.survey,
            'form': form,
            'already_submitted': result.submitted_at is not None,
        },
    )


@login_required
def survey_results(request, survey_id):
    survey = get_object_or_404(
        Survey,
        pk=survey_id,
    )

    results = SurveyResult.objects.filter(survey=survey)
    completed = results.filter(submitted_at__isnull=False)

    stats = results.aggregate(
        total=Count("pk"),
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

    answer_summary = completed.aggregate(
        participation_level_sum=Sum("participation_level"),
        participation_level_average=Avg("participation_level"),
    )

    counts = {
        row['participation_level']: row['count']
        for row in results.exclude(participation_level=None)
        .values('participation_level')
        .annotate(count=Count('id'))
    }

    distribution = [
        {
            'participation_level': value,
            'count': counts.get(value, 0),
        }
        for value in range(max(counts) + 1)
    ]

    return render(request, "juntagrico_pulse/results.html", {
        "survey": survey,
        "stats": stats,
        "answer_summary": answer_summary,
        "distribution": distribution,
    })


@login_required
def survey_individual_results(request, survey_id):
    survey = get_object_or_404(
        Survey,
        pk=survey_id,
    )

    results = SurveyResult.objects.filter(survey=survey)

    return render(request, "juntagrico_pulse/individual_results.html", {
        "survey": survey,
        "results": results,
    })


@login_required
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
