from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "Gestion des besoins – Administration"
admin.site.site_title = "Besoins ANG"
admin.site.index_title = "Administration de la plateforme"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("connexion/", auth_views.LoginView.as_view(), name="login"),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    path("mot-de-passe/", auth_views.PasswordChangeView.as_view(), name="password_change"),
    path("mot-de-passe/ok/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("", include("gestion.urls")),
]
