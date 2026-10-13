from django.contrib import admin

from . import models as m


class LigneDemandeInline(admin.TabularInline):
    model = m.LigneDemande
    extra = 0
    readonly_fields = ("prix_unitaire", "quantite_servie")


class LigneCommandeInline(admin.TabularInline):
    model = m.LigneCommande
    extra = 0
    readonly_fields = ("quantite_recue",)


@admin.register(m.Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("reference", "designation", "categorie", "type_article", "stock", "seuil_alerte", "prix_reference", "actif")
    list_filter = ("categorie", "type_article", "actif")
    search_fields = ("reference", "designation")
    readonly_fields = ("reference", "stock")


@admin.register(m.Demande)
class DemandeAdmin(admin.ModelAdmin):
    list_display = ("reference", "objet", "structure", "exercice", "statut", "priorite", "cree_le")
    list_filter = ("statut", "priorite", "exercice", "structure")
    search_fields = ("reference", "objet")
    inlines = [LigneDemandeInline]


@admin.register(m.Commande)
class CommandeAdmin(admin.ModelAdmin):
    list_display = ("reference", "fournisseur", "statut", "date_commande", "date_livraison_prevue")
    list_filter = ("statut",)
    inlines = [LigneCommandeInline]


@admin.register(m.Equipement)
class EquipementAdmin(admin.ModelAdmin):
    list_display = ("reference", "article", "numero_serie", "statut", "etat", "structure", "beneficiaire")
    list_filter = ("statut", "etat", "structure")
    search_fields = ("reference", "numero_serie", "beneficiaire")


@admin.register(m.MouvementStock)
class MouvementAdmin(admin.ModelAdmin):
    list_display = ("date", "article", "type_mvt", "quantite", "stock_apres", "structure", "utilisateur")
    list_filter = ("type_mvt",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(m.CategorieArticle)
admin.site.register(m.Fournisseur)
admin.site.register(m.EnveloppeBudgetaire)
admin.site.register(m.Livraison)
