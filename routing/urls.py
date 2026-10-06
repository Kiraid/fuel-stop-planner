from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/cities', views.cities, name='cities'),
    path('api/route', views.route, name='route'),
]
