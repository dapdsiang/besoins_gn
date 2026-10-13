from django.core.management.base import BaseCommand
from django.utils import timezone

from comptes.models import Structure, Utilisateur
from gestion.models import (Article, CategorieArticle, EnveloppeBudgetaire, Equipement, Fournisseur)
from gestion.services import mouvement
from gestion.models import MouvementStock

# (code, nom, type, code parent)
STRUCTURES = [
    ("SG", "Secrétariat général", "CAB", None),
    ("DAF", "Direction des Affaires Administratives et Financières", "DIR", "SG"),
    ("DSI", "Direction des Systèmes d'Information", "DIR", "SG"),
    ("DSL", "Direction des Services Législatifs", "DIR", "SG"),
    ("COM-LOIS", "Commission des Lois et de l'Administration", "COM", None),
    ("COM-FIN", "Commission des Finances et du Budget", "COM", None),
]
CATEGORIES = ["Fournitures de bureau", "Consommables d'impression", "Informatique", "Mobilier", "Entretien et hygiène"]
# (désignation, catégorie, type, unité, prix GNF (exemple), seuil, cible, stock initial)
ARTICLES = [
    ("Papier A4 80 g (ramette)", "Fournitures de bureau", "CONSO", "ramette", 55000, 50, 200, 120),
    ("Stylos à bille (boîte de 50)", "Fournitures de bureau", "CONSO", "boîte", 90000, 5, 20, 12),
    ("Chemises cartonnées (paquet de 100)", "Fournitures de bureau", "CONSO", "paquet", 120000, 5, 15, 3),
    ("Toner imprimante laser noir", "Consommables d'impression", "CONSO", "unité", 650000, 4, 12, 6),
    ("Cartouche d'encre couleur", "Consommables d'impression", "CONSO", "unité", 250000, 4, 10, 2),
    ("Savon liquide (litre)", "Entretien et hygiène", "CONSO", "litre", 30000, 10, 40, 25),
    ("Ordinateur portable", "Informatique", "EQUIP", "unité", 9500000, 1, 5, 0),
    ("Imprimante laser réseau", "Informatique", "EQUIP", "unité", 4200000, 1, 3, 0),
    ("Chaise de bureau ergonomique", "Mobilier", "EQUIP", "unité", 850000, 2, 10, 0),
]
UTILISATEURS = [  # (identifiant, prénom, nom, rôle, code structure, admin)
    ("admin", "Admin", "Système", "ADMIN", "SG", True),
    ("sg", "Secrétaire", "Général", "ARBITRE", "SG", False),
    ("resp_dsi", "Responsable", "DSI", "RESPONSABLE", "DSI", False),
    ("agent_dsi", "Agent", "DSI", "DEMANDEUR", "DSI", False),
    ("resp_lois", "Président", "Commission des Lois", "RESPONSABLE", "COM-LOIS", False),
    ("agent_lois", "Agent", "Commission des Lois", "DEMANDEUR", "COM-LOIS", False),
    ("acheteur", "Responsable", "Achats", "ACHETEUR", "DAF", False),
    ("magasinier", "Gestionnaire", "Stock", "MAGASINIER", "DAF", False),
    ("auditeur", "Contrôleur", "Interne", "AUDITEUR", "SG", False),
]


class Command(BaseCommand):
    help = "Données de démonstration (NE PAS utiliser en production) : structures, comptes, catalogue, budgets."

    def add_arguments(self, parser):
        parser.add_argument("--mot-de-passe", default="Ang@Demo2026!")

    def handle(self, *args, **opts):
        st = {}
        for code, nom, type_, parent in STRUCTURES:
            st[code], _ = Structure.objects.get_or_create(code=code, defaults={
                "nom": nom, "type": type_, "parent": st.get(parent)})
        for ident, prenom, nom, role, code, adm in UTILISATEURS:
            u, cree = Utilisateur.objects.get_or_create(username=ident, defaults={
                "first_name": prenom, "last_name": nom, "role": role, "structure": st[code],
                "is_staff": adm, "is_superuser": adm})
            if cree:
                u.set_password(opts["mot_de_passe"])
                u.save()
        magasinier = Utilisateur.objects.get(username="magasinier")
        cats = {n: CategorieArticle.objects.get_or_create(nom=n)[0] for n in CATEGORIES}
        for des, cat, typ, unite, prix, seuil, cible, stock in ARTICLES:
            a, cree = Article.objects.get_or_create(designation=des, defaults={
                "categorie": cats[cat], "type_article": typ, "unite": unite, "prix_reference": prix,
                "seuil_alerte": seuil, "stock_max": cible})
            if cree and stock:
                mouvement(a, MouvementStock.Type.AJUSTEMENT, stock, magasinier, motif="Stock initial (démonstration)")
        annee = timezone.localdate().year
        budgets = {"SG": 15_000_000, "DAF": 25_000_000, "DSI": 40_000_000, "DSL": 12_000_000,
                   "COM-LOIS": 10_000_000, "COM-FIN": 10_000_000}
        for code, montant in budgets.items():
            EnveloppeBudgetaire.objects.get_or_create(exercice=annee, structure=st[code],
                                                      defaults={"montant_alloue": montant})
        Fournisseur.objects.get_or_create(nom="Fournisseur Exemple 1 (fictif)", defaults={"telephone": "+224 000 00 00 00"})
        Fournisseur.objects.get_or_create(nom="Fournisseur Exemple 2 (fictif)")
        self.stdout.write(self.style.SUCCESS(
            f"Données créées. Comptes : {', '.join(u[0] for u in UTILISATEURS)} — mot de passe : {opts['mot_de_passe']}"))
        self.stdout.write(self.style.WARNING("Prix, budgets et fournisseurs sont fictifs. Changez les mots de passe."))
