from django.urls import path

from apps.arshin import api

urlpatterns = [
    path("queue/", api.QueueView.as_view(), name="arshin_queue"),
    path("submissions/", api.SubmissionListView.as_view(), name="arshin_submissions"),
    path("submissions/<int:pk>/", api.SubmissionDetailView.as_view(), name="arshin_submission"),
    path("submissions/<int:pk>/xml/", api.SubmissionXmlView.as_view(), name="arshin_submission_xml"),
    path("submissions/<int:pk>/<str:action>/", api.SubmissionActionView.as_view(), name="arshin_submission_action"),
    path("items/<int:pk>/result/", api.ItemResultView.as_view(), name="arshin_item_result"),
]
