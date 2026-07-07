# finances/signals.py
from django.db.models.signals import post_save, post_migrate
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Account

User = get_user_model()


@receiver(post_save, sender=User)
def create_agent_account(sender, instance, created, **kwargs):
    """
    Crée automatiquement un compte pour chaque nouvel agent
    """
    if created and instance.role == 'agent':
        # Vérifier si un compte existe déjà (évite les doublons)
        if not Account.objects.filter(user=instance, account_type='agent').exists():
            Account.objects.create(
                user=instance,
                account_type='agent',
                balance=0,
                currency='XOF'
            )
            print(f"✅ Compte agent créé pour {instance.email}")


@receiver(post_save, sender=User)
def update_agent_account_on_role_change(sender, instance, **kwargs):
    """
    Met à jour ou crée un compte si le rôle change vers 'agent'
    """
    if instance.role == 'agent':
        if not Account.objects.filter(user=instance, account_type='agent').exists():
            Account.objects.create(
                user=instance,
                account_type='agent',
                balance=0,
                currency='XOF'
            )
            print(
                f"✅ Compte agent créé pour {instance.email} (changement de rôle)")


@receiver(post_migrate)
def create_global_account(sender, **kwargs):
    """
    Crée le compte global après les migrations si il n'existe pas
    """
    if sender.name == 'finances':  # Remplacez par le nom de votre app
        try:
            Account.objects.get_or_create(
                account_type='global',
                defaults={
                    'balance': 0,
                    'currency': 'XOF',
                    'partner': None,
                    'user': None
                }
            )
            print("✅ Compte Global créé ou déjà existant")
        except Exception as e:
            print(f"⚠️ Erreur lors de la création du compte global: {e}")
