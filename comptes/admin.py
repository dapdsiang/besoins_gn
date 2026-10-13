from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Structure, Utilisateur


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = ("nom", "code", "type", "parent")
    list_filter = ("type",)
    search_fields = ("nom", "code")


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = ("username", "get_full_name", "role", "structure", "is_active")
    list_filter = ("role", "structure", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Profil", {"fields": ("role", "structure", "fonction", "telephone")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Profil", {"fields": ("first_name", "last_name", "email", "role", "structure", "fonction")}),)
