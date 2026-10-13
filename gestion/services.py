"""Règles métier : mouvements de stock, planification des achats."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import (Article, Commande, Demande, LigneCommande, LigneDemande, MouvementStock)

T = MouvementStock.Type
SIGNES = {T.ENTREE: 1, T.RETOUR: 1, T.SORTIE: -1, T.PERTE: -1}


class StockInsuffisant(Exception):
    pass


@transaction.atomic
def mouvement(article, type_mvt, quantite, user, *, structure=None, demande=None, bon_sortie=None,
              livraison=None, motif="", prix=None):
    """Enregistre un mouvement et met à jour le stock de façon atomique.
    `quantite` est positive, sauf pour un AJUSTEMENT où elle est signée."""
    if quantite == 0:
        return None
    art = Article.objects.select_for_update().get(pk=article.pk)
    delta = quantite if type_mvt == T.AJUSTEMENT else SIGNES[type_mvt] * abs(quantite)
    if art.stock + delta < 0:
        raise StockInsuffisant(f"Stock insuffisant pour « {art.designation} » "
                               f"(disponible : {art.stock}, demandé : {abs(delta)}).")
    art.stock += delta
    art.save(update_fields=["stock"])
    prix = art.prix_reference if prix is None else prix
    return MouvementStock.objects.create(
        article=art, type_mvt=type_mvt, quantite=delta, stock_apres=art.stock, prix_unitaire=prix,
        valeur=abs(delta) * prix, structure=structure, demande=demande, bon_sortie=bon_sortie,
        livraison=livraison, motif=motif[:250], utilisateur=user)


def plan_achats():
    """Plan d'achat suggéré = ce qu'il faut commander pour couvrir les besoins approuvés non servis
    et reconstituer le stock cible, en tenant compte des commandes déjà en cours."""
    besoins = defaultdict(int)
    for l in LigneDemande.objects.filter(
            demande__statut__in=[Demande.Statut.APPROUVEE, Demande.Statut.SERVIE_PARTIEL]):
        besoins[l.article_id] += l.reste_a_servir
    en_attente = defaultdict(int)
    for l in LigneDemande.objects.filter(
            demande__statut__in=[Demande.Statut.SOUMISE, Demande.Statut.VALIDEE]):
        en_attente[l.article_id] += l.quantite_demandee
    en_commande = defaultdict(int)
    for l in LigneCommande.objects.filter(commande__statut__in=[Commande.Statut.ENVOYEE, Commande.Statut.PARTIELLE]):
        en_commande[l.article_id] += l.reste

    lignes = []
    for a in Article.objects.filter(actif=True).select_related("categorie"):
        b, c, p = besoins[a.pk], en_commande[a.pk], en_attente[a.pk]
        projete = a.stock + c - b
        cible = a.stock_max or (a.seuil_alerte * 2)
        if projete < a.seuil_alerte or projete < 0:
            a_commander = max(cible - projete, 0, -projete)
        else:
            a_commander = 0
        if a_commander > 0:
            lignes.append({"article": a, "stock": a.stock, "besoins": b, "en_commande": c,
                           "en_attente": p, "projete": projete, "seuil": a.seuil_alerte,
                           "a_commander": a_commander, "montant": a_commander * a.prix_reference})
    lignes.sort(key=lambda r: (r["projete"] >= 0, r["projete"]))
    return lignes
