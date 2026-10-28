from django.core.validators import MinValueValidator, MaxValueValidator, StepValueValidator
from django.db import models
from django.db.models import Q


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
        'Ernährte Personen', null=True, blank=True, validators=[MaxValueValidator(999)]
    )
    subscription_percent = models.PositiveSmallIntegerField(
        'Abdeckung des Gemüsebedarfs (%)',
        null=True,
        blank=True,
        validators=[MinValueValidator(5), MaxValueValidator(100), StepValueValidator(5)],
    )
    # Derived in save(): people_eating_product * subscription_percent / 100
    consumption_level = models.FloatField('Ernährte Personen (Äquivalent)', null=True, blank=True)
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

    def save(self, *args, **kwargs):
        if self.subscription_id is None:
            self.people_eating_product = None
            self.subscription_percent = None
        if self.people_eating_product is None or self.subscription_percent is None:
            self.consumption_level = None
        else:
            self.consumption_level = self.people_eating_product * self.subscription_percent / 100
        super().save(*args, **kwargs)


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
