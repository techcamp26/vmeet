
"""
ASGI config for zoom_project project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""
"""
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import meetings.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'zoom_project.settings')

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            meetings.routing.websocket_urlpatterns
        )
    ),
})

"""



import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'zoom_project.settings')

from django.core.asgi import get_asgi_application
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

django_asgi_app = get_asgi_application()

import meetings.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            meetings.routing.websocket_urlpatterns
        )
    ),
})