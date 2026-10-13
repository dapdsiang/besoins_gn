from django.contrib.auth.models import AbstractUser
from django.db import models


class Structure(models.Model):
    """Direction, commission ou service. La hiérarchie (parent) sert à la validation."""

    class Type(models.TextChoices):
        DIRECTION = "DIR", "Direction"
        COMMISSION = "COM", "Commission"
        SERVICE = "SRV", "Service"
        CABINET = "CAB", "Cabinet / Secrétariat"

    nom = models.CharField("Nom", max_length=250)
    code = models.CharField("Code", max_length=20, unique=True)
    type = models.CharField("Type", max_length=3, choices=Type.choices, default=Type.DIRECTION)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="enfants", verbose_name="Structure de rattachement")

    class Meta:
        ordering = ["nom"]
        verbose_name = "Direction / Commission"
        verbose_name_plural = "Directions / Commissions"

    def __str__(self):
        return self.nom

    def descendants_ids(self):
        ids, a_voir = [self.pk], [self.pk]
        while a_voir:
            enfants = list(Structure.objects.filter(parent_id__in=a_voir).values_list("pk", flat=True))
            enfants = [e for e in enfants if e not in ids]
            ids += enfants
            a_voir = enfants
        return ids


class Utilisateur(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrateur système"
        DEMANDEUR = "DEMANDEUR", "Agent demandeur"
        RESPONSABLE = "RESPONSABLE", "Responsable (directeur / président de commission)"
        ARBITRE = "ARBITRE", "Arbitre budgétaire (Secrétariat général / DAF)"
        ACHETEUR = "ACHETEUR", "Responsable des achats"
        MAGASINIER = "MAGASINIER", "Magasinier / gestionnaire de stock"
        AUDITEUR = "AUDITEUR", "Auditeur / contrôleur (lecture)"

    role = models.CharField("Rôle", max_length=12, choices=Role.choices, default=Role.DEMANDEUR)
    structure = models.ForeignKey(Structure, verbose_name="Direction / Commission", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="membres")
    fonction = models.CharField("Fonction", max_length=150, blank=True)
    telephone = models.CharField("Téléphone", max_length=30, blank=True)

    class Meta:
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self):
        return self.get_full_name() or self.username

    # --- Droits métier ---
    @property
    def est_admin(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def vue_globale(self):
        return self.est_admin or self.role in (self.Role.ARBITRE, self.Role.ACHETEUR,
                                               self.Role.MAGASINIER, self.Role.AUDITEUR)

    @property
    def peut_demander(self):
        return bool(self.structure_id) and self.role != self.Role.AUDITEUR

    @property
    def peut_arbitrer(self):
        return self.est_admin or self.role == self.Role.ARBITRE

    @property
    def peut_commander(self):
        return self.est_admin or self.role == self.Role.ACHETEUR

    @property
    def peut_gerer_stock(self):
        return self.est_admin or self.role == self.Role.MAGASINIER

    @property
    def peut_gerer_catalogue(self):
        return self.est_admin or self.role in (self.Role.MAGASINIER, self.Role.ACHETEUR)

    def structures_gerees_ids(self):
        """Structures dont l'utilisateur voit les données : la sienne (+ ses sous-structures
        s'il est responsable)."""
        if not self.structure_id:
            return []
        if self.role == self.Role.RESPONSABLE:
            return self.structure.descendants_ids()
        return [self.structure_id]
