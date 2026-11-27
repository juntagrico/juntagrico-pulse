from django.core.validators import MinValueValidator, MaxValueValidator, StepValueValidator
from django.db import models
from django.db.models import Q
from juntagrico.util.models import q_isactive

# weight of a child (up to 12 years) in the fed-people equivalent
CHILD_WEIGHT = 0.5

PERCENT_VALIDATORS = [MinValueValidator(5), MaxValueValidator(100), StepValueValidator(5)]


class Survey(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Entwurf'
        PUBLISHED = 'published', 'Veröffentlicht'
        CLOSED = 'closed', 'Geschlossen'

    year = models.PositiveSmallIntegerField(
        'Umfragejahr',
        unique=True,
        validators=[MinValueValidator(2000), MaxValueValidator(9999)],
    )
    created_at = models.DateTimeField('Erstellt am', auto_now_add=True)
    status = models.CharField(
        'Status',
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    introduction = models.TextField('Einleitung', blank=True)

    def __str__(self):
        return f'Umfrage {self.year}'


class SurveyProduct(models.Model):
    """A product whose coverage is asked for in a survey.

    The question is only asked for subscriptions with an active part of one of the selected bundles.
    """
    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name='products',
    )
    name = models.CharField('Produktname', max_length=100)
    bundles = models.ManyToManyField(
        'juntagrico.SubscriptionBundle',
        related_name='survey_products',
        verbose_name='Pakete',
    )
    sort_order = models.PositiveIntegerField('Reihenfolge', default=0)

    class Meta:
        verbose_name = 'Umfrage-Produkt'
        verbose_name_plural = 'Umfrage-Produkte'
        ordering = ['sort_order', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'name'],
                name='unique_product_name_per_survey',
            ),
        ]

    def __str__(self):
        return self.name


class SurveyResultManager(models.Manager):
    def create_with_members(self, survey, members, **kwargs):
        if not members:
            return None

        result = self.model.objects.create(survey=survey, **kwargs)
        SurveyResultMember.objects.bulk_create(
            [SurveyResultMember(result=result, member=member) for member in members]
        )
        return result


class SurveyResult(models.Model):
    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name='results',
    )
    subscription = models.ForeignKey(
        'juntagrico.Subscription',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='survey_results',
    )

    members = models.ManyToManyField(
        'juntagrico.Member',
        through='SurveyResultMember',
        related_name='covered_survey_results',
    )

    submitted_by = models.ForeignKey(
        'juntagrico.Member',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submitted_survey_results',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Fixed question fields; empty until submitted.
    participation_level = models.PositiveSmallIntegerField(
        'Mitarbeitende Personen', null=True, blank=True, validators=[MaxValueValidator(999)]
    )
    # Only for results with a subscription.
    people_eating_product = models.PositiveSmallIntegerField(
        'Ernährte Personen über 12', null=True, blank=True, validators=[MaxValueValidator(999)]
    )
    children_eating_product = models.PositiveSmallIntegerField(
        'Ernährte Kinder bis 12', null=True, blank=True, validators=[MaxValueValidator(999)]
    )
    comments = models.TextField(blank=True)

    objects = SurveyResultManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'subscription'],
                condition=Q(subscription__isnull=False),
                name='unique_subscription_result_per_survey',
            ),
        ]

    @property
    def is_complete(self):
        return self.submitted_at is not None

    @property
    def people_equivalent(self):
        if self.people_eating_product is None or self.children_eating_product is None:
            return None
        return self.people_eating_product + CHILD_WEIGHT * self.children_eating_product

    def products(self):
        """The survey products asked for: those with a bundle of an active part of the subscription."""
        if self.subscription_id is None:
            return SurveyProduct.objects.none()
        return self.survey.products.filter(
            bundles__types__subscription_parts__in=self.subscription.parts.filter(q_isactive())
        ).distinct()

    def save(self, *args, **kwargs):
        if self.subscription_id is None:
            self.people_eating_product = None
            self.children_eating_product = None
        super().save(*args, **kwargs)


class SurveyProductCoverage(models.Model):
    result = models.ForeignKey(
        SurveyResult,
        on_delete=models.CASCADE,
        related_name='product_coverages',
    )
    product = models.ForeignKey(
        SurveyProduct,
        on_delete=models.CASCADE,
        related_name='coverages',
        verbose_name='Produkt',
    )
    percent = models.PositiveSmallIntegerField('Abdeckung des Bedarfs (%)', validators=PERCENT_VALIDATORS)

    class Meta:
        verbose_name = 'Versorgungsgrad'
        verbose_name_plural = 'Versorgungsgrade'
        ordering = ['product__sort_order', 'product__pk']
        constraints = [
            models.UniqueConstraint(
                fields=['result', 'product'],
                name='unique_coverage_per_result_and_product',
            ),
        ]

    def __str__(self):
        return f'{self.product}: {self.percent}%'


class SurveyResultMember(models.Model):
    result = models.ForeignKey(
        SurveyResult,
        on_delete=models.CASCADE,
        related_name='member_links',
    )
    member = models.ForeignKey(
        'juntagrico.Member',
        on_delete=models.CASCADE,
        related_name='survey_result_links',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['result', 'member'],
                name='unique_member_per_survey_result',
            ),
        ]
        indexes = [
            models.Index(fields=['member', 'result']),
        ]
