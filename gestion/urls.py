from django.urls import path

from . import views

urlpatterns = [
    path("", views.accueil, name="accueil"),
    # Demandes
    path("demandes/", views.demandes, name="demandes"),
    path("demandes/nouvelle/", views.demande_creer, name="demande_creer"),
    path("demandes/<int:pk>/", views.demande_detail, name="demande_detail"),
    path("demandes/<int:pk>/modifier/", views.demande_modifier, name="demande_modifier"),
    path("demandes/<int:pk>/action/<str:action>/", views.demande_action, name="demande_action"),
    path("demandes/<int:pk>/rejeter/", views.demande_rejeter, name="demande_rejeter"),
    path("demandes/<int:pk>/arbitrer/", views.demande_arbitrer, name="demande_arbitrer"),
    path("demandes/<int:pk>/servir/", views.demande_servir, name="demande_servir"),
    path("bons-sortie/<int:pk>/", views.bon_sortie, name="bon_sortie"),
    # Catalogue / stock
    path("stock/", views.stock, name="stock"),
    path("stock/mouvements/", views.mouvements, name="mouvements"),
    path("stock/perte/", views.perte, name="perte"),
    path("stock/ajustement/", views.ajustement, name="ajustement"),
    path("articles/nouveau/", views.article_form, name="article_creer"),
    path("articles/<int:pk>/modifier/", views.article_form, name="article_modifier"),
    # Achats
    path("fournisseurs/", views.fournisseurs, name="fournisseurs"),
    path("fournisseurs/nouveau/", views.fournisseur_form, name="fournisseur_creer"),
    path("fournisseurs/<int:pk>/modifier/", views.fournisseur_form, name="fournisseur_modifier"),
    path("commandes/", views.commandes, name="commandes"),
    path("commandes/nouvelle/", views.commande_form, name="commande_creer"),
    path("commandes/<int:pk>/", views.commande_detail, name="commande_detail"),
    path("commandes/<int:pk>/modifier/", views.commande_form, name="commande_modifier"),
    path("commandes/<int:pk>/action/<str:action>/", views.commande_action, name="commande_action"),
    path("commandes/<int:pk>/reception/", views.reception, name="reception"),
    path("livraisons/", views.livraisons, name="livraisons"),
    # Équipements
    path("equipements/", views.equipements, name="equipements"),
    path("equipements/nouveau/", views.equipement_creer, name="equipement_creer"),
    path("equipements/<int:pk>/", views.equipement_detail, name="equipement_detail"),
    path("equipements/<int:pk>/affecter/", views.equipement_affecter, name="equipement_affecter"),
    path("equipements/<int:pk>/action/<str:action>/", views.equipement_action, name="equipement_action"),
    # Budget et rapports
    path("budgets/", views.budgets, name="budgets"),
    path("budgets/nouveau/", views.enveloppe_form, name="enveloppe_creer"),
    path("budgets/<int:pk>/modifier/", views.enveloppe_form, name="enveloppe_modifier"),
    path("rapports/consommation/", views.rapport_consommation, name="rapport_consommation"),
    path("rapports/budget/", views.rapport_budget, name="rapport_budget"),
    path("rapports/pertes/", views.rapport_pertes, name="rapport_pertes"),
    path("rapports/planification/", views.planification, name="planification"),
]
