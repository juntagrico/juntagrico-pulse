from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q

from .forms import BaseSurveyProductFormSet, SurveyProductForm
from .models import Survey, SurveyProduct, SurveyProductCoverage, SurveyResult, SurveyResultMember


class SurveyResultMemberInline(admin.TabularInline):
    model = SurveyResultMember
    extra = 0
    autocomplete_fields = ('member',)


class SurveyProductInline(admin.TabularInline):
    model = SurveyProduct
    # same validation as on the review page: products with answers keep their bundles and can't be deleted
    form = SurveyProductForm
    formset = BaseSurveyProductFormSet
    extra = 0

    # like on the review page, products can only be changed while the survey is a draft:
    # deleting one would delete its answers and changing its bundles would change who is asked
    @staticmethod
    def is_draft(obj):
        return obj is None or obj.status == Survey.Status.DRAFT

    def has_add_permission(self, request, obj=None):
        return self.is_draft(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self.is_draft(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.is_draft(obj) and super().has_delete_permission(request, obj)


class SurveyProductCoverageInline(admin.TabularInline):
    model = SurveyProductCoverage
    extra = 0

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # only offer the products of the result's survey
        if db_field.name == 'product':
            object_id = request.resolver_match.kwargs.get('object_id')
            if object_id is None:
                kwargs['queryset'] = SurveyProduct.objects.none()
            else:
                kwargs['queryset'] = SurveyProduct.objects.filter(survey__results__pk=object_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ('year', 'created_at', 'status', 'submitted_count', 'result_count')
    list_filter = ('year',)
    search_fields = ('year',)
    ordering = ('-year',)
    readonly_fields = ('created_at',)
    inlines = (SurveyProductInline,)

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
    inlines = (SurveyResultMemberInline, SurveyProductCoverageInline)
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
                    'people_eating_product',
                    'children_eating_product',
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
            SurveyProductCoverage.objects.filter(result__in=queryset).delete()
            updated = queryset.update(
                submitted_at=None,
                submitted_by=None,
                participation_level=None,
                people_eating_product=None,
                children_eating_product=None,
                comments='',
            )

        self.message_user(
            request,
            f'{updated} Antwort(en) zurückgesetzt.',
            level=messages.SUCCESS,
        )
