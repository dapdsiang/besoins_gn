from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

MONTANT = dict(max_digits=16, decimal_places=0)
SORTIE_MONTANT = ExpressionWrapper(F("quantite_accordee") * F("prix_unitaire"),
                                   output_field=DecimalField(max_digits=18, decimal_places=0))
SORTIE_SERVIE = ExpressionWrapper(F("quantite_servie") * F("prix_unitaire"),
                                  output_field=DecimalField(max_digits=18, decimal_places=0))


def annee_courante():
    return timezone.localdate().year


class AvecReference(models.Model):
    """Référence automatique : PREFIXE-AAAA-NNNNN."""
    PREFIXE = "REF"
    LARGEUR = 5
    ANNEE = True
    reference = models.CharField("Référence", max_length=24, unique=True, null=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        creation = self.pk is None
        super().save(*args, **kwargs)
        if creation and not self.reference:
            annee = f"{timezone.now():%Y}-" if self.ANNEE else ""
            self.reference = f"{self.PREFIXE}-{annee}{self.pk:0{self.LARGEUR}d}"
            super().save(update_fields=["reference"])


# ------------------------------------------------------------------ Catalogue
class CategorieArticle(models.Model):
    nom = models.CharField("Nom", max_length=120, unique=True)

    class Meta:
        ordering = ["nom"]
        verbose_name = "Catégorie d'article"
        verbose_name_plural = "Catégories d'articles"

    def __str__(self):
        return self.nom


class Fournisseur(models.Model):
    nom = models.CharField("Raison sociale", max_length=200)
    identifiant_fiscal = models.CharField("NIF / RCCM", max_length=60, blank=True)
    contact = models.CharField("Personne à contacter", max_length=150, blank=True)
    telephone = models.CharField("Téléphone", max_length=40, blank=True)
    email = models.EmailField("E-mail", blank=True)
    adresse = models.CharField("Adresse", max_length=250, blank=True)
    actif = models.BooleanField("Actif", default=True)

    class Meta:
        ordering = ["nom"]
        verbose_name = "Fournisseur"
        verbose_name_plural = "Fournisseurs"

    def __str__(self):
        return self.nom


class Article(AvecReference):
    PREFIXE, LARGEUR, ANNEE = "ART", 4, False

    class Type(models.TextChoices):
        CONSOMMABLE = "CONSO", "Consommable"
        EQUIPEMENT = "EQUIP", "Équipement (matériel inventorié)"

    class Unite(models.TextChoices):
        UNITE = "unité", "Unité"
        RAMETTE = "ramette", "Ramette"
        BOITE = "boîte", "Boîte"
        CARTON = "carton", "Carton"
        PAQUET = "paquet", "Paquet"
        LITRE = "litre", "Litre"
        LOT = "lot", "Lot"

    designation = models.CharField("Désignation", max_length=250)
    categorie = models.ForeignKey(CategorieArticle, on_delete=models.PROTECT, related_name="articles",
                                  verbose_name="Catégorie")
    type_article = models.CharField("Type", max_length=5, choices=Type.choices, default=Type.CONSOMMABLE)
    unite = models.CharField("Unité", max_length=10, choices=Unite.choices, default=Unite.UNITE)
    prix_reference = models.DecimalField("Prix unitaire de référence (GNF)", default=0, **MONTANT)
    seuil_alerte = models.PositiveIntegerField("Seuil d'alerte (stock minimum)", default=0)
    stock_max = models.PositiveIntegerField("Niveau cible de réapprovisionnement", default=0,
                                            help_text="Stock visé après réapprovisionnement")
    stock = models.IntegerField("Stock actuel", default=0, editable=False)
    actif = models.BooleanField("Actif", default=True)

    class Meta:
        ordering = ["designation"]
        verbose_name = "Article"
        verbose_name_plural = "Articles"

    def __str__(self):
        return f"{self.reference} – {self.designation} ({self.unite})"

    @property
    def est_equipement(self):
        return self.type_article == self.Type.EQUIPEMENT

    @property
    def niveau(self):
        if self.stock <= 0:
            return ("Rupture", "danger")
        if self.seuil_alerte and self.stock <= self.seuil_alerte:
            return ("Alerte", "warning text-dark")
        return ("OK", "success")

    @property
    def valeur_stock(self):
        return self.stock * self.prix_reference


# ------------------------------------------------------------------ Budget
class EnveloppeBudgetaire(models.Model):
    exercice = models.PositiveIntegerField("Exercice", default=annee_courante)
    structure = models.ForeignKey("comptes.Structure", on_delete=models.CASCADE, related_name="enveloppes",
                                  verbose_name="Direction / Commission")
    montant_alloue = models.DecimalField("Montant alloué (GNF)", **MONTANT)
    observations = models.CharField("Observations", max_length=250, blank=True)

    class Meta:
        ordering = ["-exercice", "structure__nom"]
        unique_together = [("exercice", "structure")]
        verbose_name = "Enveloppe budgétaire"
        verbose_name_plural = "Enveloppes budgétaires"

    def __str__(self):
        return f"{self.structure} – {self.exercice}"

    @property
    def engage(self):
        return Demande.engage(self.exercice, self.structure)

    @property
    def consomme(self):
        return (MouvementStock.objects.filter(type_mvt=MouvementStock.Type.SORTIE, structure=self.structure,
                                              date__year=self.exercice).aggregate(t=Sum("valeur"))["t"]
                or Decimal(0))

    @property
    def disponible(self):
        return self.montant_alloue - self.engage

    @property
    def taux_engage(self):
        return int(self.engage * 100 / self.montant_alloue) if self.montant_alloue else 0


# ------------------------------------------------------------------ Demandes (expression des besoins)
class DemandeQuerySet(models.QuerySet):
    def visibles_pour(self, user):
        if user.est_admin:
            return self
        brouillon = ~Q(statut=Demande.Statut.BROUILLON) | Q(demandeur=user)
        if user.vue_globale:
            return self.filter(brouillon)
        return self.filter(brouillon & (Q(structure_id__in=user.structures_gerees_ids()) | Q(demandeur=user)))


class Demande(AvecReference):
    PREFIXE, LARGEUR = "BES", 5

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        SOUMISE = "SOUMISE", "Soumise (validation hiérarchique)"
        VALIDEE = "VALIDEE", "Validée (arbitrage budgétaire)"
        APPROUVEE = "APPROUVEE", "Approuvée (à servir)"
        SERVIE_PARTIEL = "SERVIE_PARTIEL", "Partiellement servie"
        SERVIE = "SERVIE", "Servie"
        REJETEE = "REJETEE", "Rejetée"
        ANNULEE = "ANNULEE", "Annulée"

    class Priorite(models.TextChoices):
        NORMALE = "NORMALE", "Normale"
        URGENTE = "URGENTE", "Urgente"
        CRITIQUE = "CRITIQUE", "Critique (continuité de service)"

    structure = models.ForeignKey("comptes.Structure", on_delete=models.PROTECT, related_name="demandes",
                                  verbose_name="Direction / Commission")
    demandeur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="demandes")
    exercice = models.PositiveIntegerField("Exercice", default=annee_courante)
    objet = models.CharField("Objet de la demande", max_length=250)
    priorite = models.CharField("Priorité", max_length=8, choices=Priorite.choices, default=Priorite.NORMALE)
    date_souhaitee = models.DateField("Date de besoin souhaitée", null=True, blank=True)
    justification = models.TextField("Justification générale", blank=True)
    statut = models.CharField("Statut", max_length=14, choices=Statut.choices, default=Statut.BROUILLON)
    motif_rejet = models.TextField("Motif du rejet", blank=True)
    commentaire_arbitrage = models.TextField("Commentaire d'arbitrage", blank=True)
    depassement_autorise = models.BooleanField("Dépassement budgétaire autorisé", default=False)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    objects = DemandeQuerySet.as_manager()
    STATUTS_ENGAGES = ("APPROUVEE", "SERVIE_PARTIEL", "SERVIE")

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = "Demande de besoins"
        verbose_name_plural = "Demandes de besoins"

    def __str__(self):
        return f"{self.reference} · {self.objet}"

    @property
    def classe_statut(self):
        return {"BROUILLON": "secondary", "SOUMISE": "warning text-dark", "VALIDEE": "info text-dark",
                "APPROUVEE": "primary", "SERVIE_PARTIEL": "primary", "SERVIE": "success",
                "REJETEE": "danger", "ANNULEE": "dark"}[self.statut]

    @property
    def classe_priorite(self):
        return {"NORMALE": "secondary", "URGENTE": "warning text-dark", "CRITIQUE": "danger"}[self.priorite]

    @property
    def montant_demande(self):
        return sum((l.montant_demande for l in self.lignes.all()), Decimal(0))

    @property
    def montant_accorde(self):
        return sum((l.montant_accorde for l in self.lignes.all()), Decimal(0))

    @classmethod
    def engage(cls, exercice, structure=None, exclure=None):
        """Montant engagé : accordé pour les demandes à servir, réellement servi pour les clôturées."""
        total = Decimal(0)
        for statuts, expr in (((cls.Statut.APPROUVEE, cls.Statut.SERVIE_PARTIEL), SORTIE_MONTANT),
                              ((cls.Statut.SERVIE,), SORTIE_SERVIE)):
            qs = LigneDemande.objects.filter(demande__exercice=exercice, demande__statut__in=statuts)
            if structure is not None:
                qs = qs.filter(demande__structure=structure)
            if exclure is not None:
                qs = qs.exclude(demande=exclure)
            total += qs.aggregate(t=Sum(expr))["t"] or Decimal(0)
        return total

    @classmethod
    def a_valider_par(cls, user):
        """File de validation hiérarchique de niveau 1."""
        qs = cls.objects.filter(statut=cls.Statut.SOUMISE)
        if user.peut_arbitrer:
            return qs
        if user.role == user.Role.RESPONSABLE:
            return qs.filter(structure_id__in=user.structures_gerees_ids()).exclude(demandeur=user)
        return qs.none()

    def peut_etre_valide_par(self, user):
        return self.statut == self.Statut.SOUMISE and Demande.a_valider_par(user).filter(pk=self.pk).exists()

    @property
    def reste_a_servir(self):
        return sum(l.reste_a_servir for l in self.lignes.all())


class LigneDemande(models.Model):
    demande = models.ForeignKey(Demande, on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="lignes_demande")
    quantite_demandee = models.PositiveIntegerField("Quantité demandée")
    quantite_accordee = models.PositiveIntegerField("Quantité accordée", null=True, blank=True)
    quantite_servie = models.PositiveIntegerField("Quantité servie", default=0)
    prix_unitaire = models.DecimalField("Prix unitaire", null=True, blank=True, editable=False, **MONTANT)
    justification = models.CharField("Justification", max_length=250, blank=True)

    class Meta:
        ordering = ["pk"]

    def save(self, *args, **kwargs):
        if self.prix_unitaire is None:
            self.prix_unitaire = self.article.prix_reference
        super().save(*args, **kwargs)

    @property
    def montant_demande(self):
        return (self.prix_unitaire or 0) * self.quantite_demandee

    @property
    def montant_accorde(self):
        return (self.prix_unitaire or 0) * (self.quantite_accordee or 0)

    @property
    def reste_a_servir(self):
        return max((self.quantite_accordee or 0) - self.quantite_servie, 0)


class HistoriqueDemande(models.Model):
    demande = models.ForeignKey(Demande, on_delete=models.CASCADE, related_name="historique")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=120)
    commentaire = models.TextField(blank=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "pk"]


# ------------------------------------------------------------------ Commandes et livraisons
class Commande(AvecReference):
    PREFIXE, LARGEUR = "CMD", 4

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        ENVOYEE = "ENVOYEE", "Envoyée au fournisseur"
        PARTIELLE = "PARTIELLE", "Livrée partiellement"
        LIVREE = "LIVREE", "Livrée"
        ANNULEE = "ANNULEE", "Annulée"

    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.PROTECT, related_name="commandes")
    date_commande = models.DateField("Date de commande", default=timezone.localdate)
    date_livraison_prevue = models.DateField("Livraison prévue le", null=True, blank=True)
    statut = models.CharField("Statut", max_length=10, choices=Statut.choices, default=Statut.BROUILLON)
    observations = models.TextField("Observations", blank=True)
    cree_par = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="commandes")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"

    def __str__(self):
        return f"{self.reference} · {self.fournisseur}"

    @property
    def classe_statut(self):
        return {"BROUILLON": "secondary", "ENVOYEE": "primary", "PARTIELLE": "warning text-dark",
                "LIVREE": "success", "ANNULEE": "dark"}[self.statut]

    @property
    def montant_total(self):
        return sum((l.montant for l in self.lignes.all()), Decimal(0))

    @property
    def en_retard(self):
        return (self.statut in (self.Statut.ENVOYEE, self.Statut.PARTIELLE) and self.date_livraison_prevue
                and self.date_livraison_prevue < timezone.localdate())

    def maj_statut_livraison(self):
        lignes = list(self.lignes.all())
        if all(l.quantite_recue >= l.quantite for l in lignes):
            self.statut = self.Statut.LIVREE
        elif any(l.quantite_recue > 0 for l in lignes):
            self.statut = self.Statut.PARTIELLE
        self.save(update_fields=["statut"])


class LigneCommande(models.Model):
    commande = models.ForeignKey(Commande, on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="lignes_commande")
    quantite = models.PositiveIntegerField("Quantité commandée")
    prix_unitaire = models.DecimalField("Prix unitaire (GNF)", null=True, blank=True, **MONTANT)
    quantite_recue = models.PositiveIntegerField("Quantité reçue", default=0)

    class Meta:
        ordering = ["pk"]

    def save(self, *args, **kwargs):
        if self.prix_unitaire is None:
            self.prix_unitaire = self.article.prix_reference
        super().save(*args, **kwargs)

    @property
    def montant(self):
        return (self.prix_unitaire or 0) * self.quantite

    @property
    def reste(self):
        return max(self.quantite - self.quantite_recue, 0)


class Livraison(AvecReference):
    PREFIXE, LARGEUR = "LIV", 4
    commande = models.ForeignKey(Commande, on_delete=models.PROTECT, related_name="livraisons")
    numero_bl = models.CharField("N° du bon de livraison fournisseur", max_length=60, blank=True)
    date_livraison = models.DateField("Date de livraison", default=timezone.localdate)
    recu_par = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="livraisons")
    observations = models.TextField("Observations (réserves, anomalies)", blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = "Livraison"
        verbose_name_plural = "Livraisons"

    def __str__(self):
        return self.reference or "Livraison"


class LigneLivraison(models.Model):
    livraison = models.ForeignKey(Livraison, on_delete=models.CASCADE, related_name="lignes")
    ligne_commande = models.ForeignKey(LigneCommande, on_delete=models.PROTECT, related_name="receptions")
    quantite = models.PositiveIntegerField()


# ------------------------------------------------------------------ Stock
class BonSortie(AvecReference):
    PREFIXE, LARGEUR = "BS", 5
    demande = models.ForeignKey(Demande, on_delete=models.PROTECT, related_name="bons_sortie")
    magasinier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bons_sortie")
    observations = models.CharField("Observations", max_length=250, blank=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]


class LigneBonSortie(models.Model):
    bon = models.ForeignKey(BonSortie, on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(Article, on_delete=models.PROTECT)
    quantite = models.PositiveIntegerField()
    prix_unitaire = models.DecimalField(**MONTANT)

    @property
    def montant(self):
        return self.prix_unitaire * self.quantite


class MouvementStock(models.Model):
    class Type(models.TextChoices):
        ENTREE = "ENTREE", "Entrée (livraison)"
        SORTIE = "SORTIE", "Sortie (distribution)"
        PERTE = "PERTE", "Perte / casse / péremption"
        AJUSTEMENT = "AJUST", "Ajustement d'inventaire"
        RETOUR = "RETOUR", "Retour en stock"

    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="mouvements")
    type_mvt = models.CharField("Type", max_length=6, choices=Type.choices)
    quantite = models.IntegerField("Quantité (signée)")
    stock_apres = models.IntegerField("Stock après")
    prix_unitaire = models.DecimalField(default=0, **MONTANT)
    valeur = models.DecimalField("Valeur", default=0, **MONTANT)
    structure = models.ForeignKey("comptes.Structure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="mouvements")
    demande = models.ForeignKey(Demande, null=True, blank=True, on_delete=models.SET_NULL, related_name="mouvements")
    bon_sortie = models.ForeignKey(BonSortie, null=True, blank=True, on_delete=models.SET_NULL)
    livraison = models.ForeignKey(Livraison, null=True, blank=True, on_delete=models.SET_NULL)
    motif = models.CharField("Motif", max_length=250, blank=True)
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name = "Mouvement de stock"
        verbose_name_plural = "Mouvements de stock"

    @property
    def classe(self):
        return "success" if self.quantite > 0 else "danger"


# ------------------------------------------------------------------ Équipements
class Equipement(AvecReference):
    PREFIXE, LARGEUR = "INV", 5

    class Statut(models.TextChoices):
        EN_STOCK = "STOCK", "En stock (non affecté)"
        AFFECTE = "AFFECTE", "Affecté"
        EN_REPARATION = "REPAR", "En réparation"
        REFORME = "REFORME", "Réformé"

    class Etat(models.TextChoices):
        NEUF = "NEUF", "Neuf"
        BON = "BON", "Bon état"
        USAGE = "USAGE", "Usagé"
        DEFECTUEUX = "DEFECT", "Défectueux"

    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="equipements",
                                limit_choices_to={"type_article": Article.Type.EQUIPEMENT})
    numero_serie = models.CharField("N° de série", max_length=100, blank=True)
    date_acquisition = models.DateField("Date d'acquisition", default=timezone.localdate)
    valeur_acquisition = models.DecimalField("Valeur d'acquisition (GNF)", default=0, **MONTANT)
    etat = models.CharField("État", max_length=6, choices=Etat.choices, default=Etat.NEUF)
    statut = models.CharField("Statut", max_length=7, choices=Statut.choices, default=Statut.EN_STOCK)
    structure = models.ForeignKey("comptes.Structure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="equipements", verbose_name="Affecté à")
    beneficiaire = models.CharField("Bénéficiaire / détenteur", max_length=150, blank=True)
    localisation = models.CharField("Localisation (bureau, salle)", max_length=150, blank=True)
    livraison = models.ForeignKey(Livraison, null=True, blank=True, on_delete=models.SET_NULL, related_name="equipements")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = "Équipement"
        verbose_name_plural = "Équipements"

    def __str__(self):
        return f"{self.reference} – {self.article.designation}"

    @property
    def classe_statut(self):
        return {"STOCK": "secondary", "AFFECTE": "success", "REPAR": "warning text-dark", "REFORME": "dark"}[self.statut]

    def affectation_courante(self):
        return self.affectations.filter(date_fin__isnull=True).first()

    def _clore_affectation(self):
        self.affectations.filter(date_fin__isnull=True).update(date_fin=timezone.localdate())

    def affecter(self, structure, beneficiaire, localisation, user, motif="", demande=None):
        self._clore_affectation()
        Affectation.objects.create(equipement=self, structure=structure, beneficiaire=beneficiaire,
                                   localisation=localisation, motif=motif, demande=demande, cree_par=user)
        self.statut, self.structure = self.Statut.AFFECTE, structure
        self.beneficiaire, self.localisation = beneficiaire, localisation
        self.save()

    def restituer(self):
        self._clore_affectation()
        self.statut, self.structure, self.beneficiaire, self.localisation = self.Statut.EN_STOCK, None, "", ""
        self.save()

    def reformer(self):
        self._clore_affectation()
        self.statut, self.structure, self.beneficiaire = self.Statut.REFORME, None, ""
        self.save()


class Affectation(models.Model):
    equipement = models.ForeignKey(Equipement, on_delete=models.CASCADE, related_name="affectations")
    structure = models.ForeignKey("comptes.Structure", on_delete=models.PROTECT, related_name="affectations")
    beneficiaire = models.CharField(max_length=150, blank=True)
    localisation = models.CharField(max_length=150, blank=True)
    date_debut = models.DateField(default=timezone.localdate)
    date_fin = models.DateField(null=True, blank=True)
    motif = models.CharField(max_length=250, blank=True)
    demande = models.ForeignKey(Demande, null=True, blank=True, on_delete=models.SET_NULL)
    cree_par = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-date_debut", "-pk"]
