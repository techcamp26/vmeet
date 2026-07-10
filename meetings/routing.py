from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/meeting/(?P<meeting_id>[a-z]{3}-[a-z]{4}-[a-z]{3})/$', consumers.MeetingConsumer.as_asgi()),
]
