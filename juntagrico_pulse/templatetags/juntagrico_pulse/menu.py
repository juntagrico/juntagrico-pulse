from django import template

from juntagrico_pulse.models import SurveyResult, Survey

register = template.Library()


@register.inclusion_tag('juntagrico_pulse/snippets/user_menu.html')
def show_pulse_menu(request):
    return {
        'survey_results': SurveyResult.objects.filter(
            survey__status=Survey.Status.PUBLISHED,
            member_links__member=request.user.member,
        )
        .select_related('survey')
        .order_by('survey__year')
    }
