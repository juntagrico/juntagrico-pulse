from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q

from .models import Survey, SurveyResult, SurveyResultMember


class SurveyResultMemberInline(admin.TabularInline):
    model = SurveyResultMember
    extra = 0
    autocomplete_fields = ('member',)


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ('year', 'created_at', 'status', 'submitted_count', 'result_count')
    list_filter = ('year',)
    search_fields = ('year',)
    ordering = ('-year',)
    readonly_fields = ('created_at',)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _submitted_count=Count(
                    'results',
                    distinct=True,
                    filter=Q(results__submitted_at__isnull=False),
                ),
                _result_count=Count('results', distinct=True),
            )
        )

    @admin.display(description='Eingereicht', ordering='_submitted_count')
    def submitted_count(self, obj):
        return obj._submitted_count

    @admin.display(description='Gesamt', ordering='_result_count')
    def result_count(self, obj):
        return obj._result_count


@admin.register(SurveyResult)
class SurveyResultAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'survey',
        'subscription',
        'members_display',
        'is_complete',
        'submitted_by',
        'submitted_at',
    )
    list_filter = ('survey', 'submitted_at')
    search_fields = (
        'survey__year',
        'subscription__id',
        'submitted_by__name',
    )
    autocomplete_fields = (
        'survey',
        'subscription',
        'submitted_by',
    )
    inlines = (SurveyResultMemberInline,)
    readonly_fields = ('submitted_at',)
    ordering = ('survey__year', 'id')

    fieldsets = (
        (
            'Zuweisung',
            {
                'fields': (
                    'survey',
                    'subscription',
                ),
            },
        ),
        (
            'Einreichung',
            {
                'fields': (
                    'submitted_by',
                    'submitted_at',
                ),
            },
        ),
        (
            'Antworten',
            {
                'fields': (
                    'participation_level',
                    'consumption_level',
                    'comments',
                ),
            },
        ),
    )
    actions = ['reset_survey_results']

    @admin.display(description='Mitglieder')
    def members_display(self, obj):
        return ', '.join(str(member) for member in obj.members.all())

    @admin.display(boolean=True, description='Vollständig', ordering='-submitted_at')
    def is_complete(self, obj):
        return obj.submitted_at is not None

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('members')

    @admin.action(description='Eingereichte Antwort zurücksetzen')
    def reset_survey_results(self, request, queryset):
        with transaction.atomic():
            updated = queryset.update(
                submitted_at=None,
                submitted_by=None,
                participation_level=None,
                consumption_level=None,
                comments='',
            )

        self.message_user(
            request,
            f'{updated} Antwort(en) zurückgesetzt.',
            level=messages.SUCCESS,
        )
