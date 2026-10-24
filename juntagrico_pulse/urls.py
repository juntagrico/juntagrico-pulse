from django.urls import path

from . import views

app_name = "juntagrico-pulse"

urlpatterns = [
    path('', views.survey_overview, name='overview'),
    path('start/', views.start_survey, name='start'),
    path('<int:survey_id>/review/', views.review_survey, name='review'),
    path(
        '<int:survey_id>/results/<int:result_id>/members/<int:member_id>/remove/',
        views.remove_survey_member,
        name='remove_member',
    ),
    path('<int:survey_id>/publish/', views.publish_survey, name='publish'),
    path('<int:survey_id>/fill/', views.fill_survey, name='fill'),
    path("<int:survey_id>/results/", views.survey_results, name="results"),
    path("<int:survey_id>/close/", views.close_survey, name="close"),
]
