from django.db import migrations
from django.db.models import F, Q
from django.db.models.functions import Coalesce


def copy_vegetable_coverage(apps, schema_editor):
    # the vegetable coverage was a single field before products could be configured per survey
    Survey = apps.get_model('juntagrico_pulse', 'Survey')
    SurveyProduct = apps.get_model('juntagrico_pulse', 'SurveyProduct')
    SurveyProductCoverage = apps.get_model('juntagrico_pulse', 'SurveyProductCoverage')
    SubscriptionBundle = apps.get_model('juntagrico', 'SubscriptionBundle')

    # surveys with answers, and open ones, which ask the vegetable question to those still answering
    surveys = Survey.objects.filter(Q(results__subscription_percent__isnull=False) | ~Q(status='closed'))
    for survey in surveys.distinct():
        product = SurveyProduct.objects.create(survey=survey, name='Gemüse')
        product.bundles.set(SubscriptionBundle.objects.all())
        SurveyProductCoverage.objects.bulk_create(
            SurveyProductCoverage(result=result, product=product, percent=result.subscription_percent)
            for result in survey.results.filter(subscription_percent__isnull=False)
        )
    # the people count used to include the children
    SurveyResult = apps.get_model('juntagrico_pulse', 'SurveyResult')
    SurveyResult.objects.filter(people_eating_product__isnull=False).update(children_eating_product=0)


def restore_vegetable_coverage(apps, schema_editor):
    # inverse of copy_vegetable_coverage: keep the coverage of the first product of each result
    SurveyResult = apps.get_model('juntagrico_pulse', 'SurveyResult')
    # the people count used to include the children
    SurveyResult.objects.filter(people_eating_product__isnull=False).update(
        people_eating_product=F('people_eating_product') + Coalesce('children_eating_product', 0),
    )
    for result in SurveyResult.objects.filter(product_coverages__isnull=False).distinct():
        coverage = result.product_coverages.order_by('product__sort_order', 'product__pk').first()
        result.subscription_percent = coverage.percent
        if result.people_eating_product is not None:
            result.consumption_level = result.people_eating_product * coverage.percent / 100
        result.save(update_fields=['subscription_percent', 'consumption_level'])


class Migration(migrations.Migration):
    # separate from the schema changes, which PostgreSQL can't run in the same transaction as this data change

    dependencies = [
        ('juntagrico_pulse', '0002_survey_products'),
    ]

    operations = [
        migrations.RunPython(copy_vegetable_coverage, restore_vegetable_coverage),
    ]
