from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse, resolve, Resolver404

from .models import SurveyResult


class RequireSurveyCompletionMiddleware:
    """
    Require authenticated members to complete any pending published survey
    before visiting other pages.
    """

    EXEMPT = (
        "fill",
        "logout",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user

        if not user.is_authenticated:
            return self.get_response(request)

        # Permit logging out and essential pages.
        try:
            if resolve(request.path_info).url_name in self.EXEMPT:
                return self.get_response(request)
        except Resolver404:
            pass

        pending_survey_id = (
            SurveyResult.objects
            .filter(
                survey__status="published",
                member_links__member=user.member,
                submitted_at__isnull=True,
            )
            .order_by("survey__year")
            .values_list("survey_id", flat=True)
            .first()
        )

        if pending_survey_id is not None:
            url = reverse("juntagrico-pulse:fill", args=[pending_survey_id])
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{url}?{query}")

        return self.get_response(request)
