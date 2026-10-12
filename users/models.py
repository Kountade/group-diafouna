from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
from django_rest_passwordreset.signals import reset_password_token_created
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is a required field')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)

    def create_admin(self, email, password=None, **extra_fields):
        """Créer un utilisateur avec le rôle admin"""
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_staff', True)
        return self.create_user(email, password, **extra_fields)

    def create_agent(self, email, password=None, **extra_fields):
        """Créer un utilisateur avec le rôle agent"""
        extra_fields.setdefault('role', 'agent')
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Administrateur'),
        ('agent', 'Agent'),
    )

    email = models.EmailField(max_length=200, unique=True)
    birthday = models.DateField(null=True, blank=True)
    username = models.CharField(max_length=200, null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    profile_picture = models.ImageField(
        upload_to='profiles/', null=True, blank=True)

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='agent'
    )

    # Champs supplémentaires pour la gestion
    is_online = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        ordering = ['-date_joined']

    def __str__(self):
        return f"{self.get_full_name() or self.email} ({self.get_role_display()})"

    # Propriétés pour vérifier les rôles facilement
    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_agent(self):
        return self.role == 'agent'

    # Propriété pour vérifier si l'utilisateur a accès à l'administration
    @property
    def has_admin_access(self):
        return self.role == 'admin'

    # Méthode pour changer le rôle
    def change_role(self, new_role):
        if new_role in dict(self.ROLE_CHOICES).keys():
            self.role = new_role
            # Mettre à jour is_staff pour le rôle admin
            if new_role == 'admin':
                self.is_staff = True
            else:
                self.is_staff = False
            self.save()
            return True
        return False

    # Méthode pour obtenir les permissions selon le rôle
    def get_permissions(self):
        permissions = {
            'admin': ['all', 'manage_users', 'view_all', 'edit_all', 'manage_system'],
            'agent': ['view_tasks', 'process_requests', 'update_status', 'view_assigned_items']
        }
        return permissions.get(self.role, [])

    # Méthode pour vérifier une permission spécifique
    def has_permission(self, permission):
        return permission in self.get_permissions()


@receiver(reset_password_token_created)
def password_reset_token_created(reset_password_token, *args, **kwargs):
    sitelink = "http://localhost:5173/"
    token = "{}".format(reset_password_token.key)
    full_link = str(sitelink) + str("password-reset/") + str(token)

    print(f"Token généré: {token}")
    print(f"Lien complet: {full_link}")

    context = {
        'full_link': full_link,
        'email_address': reset_password_token.user.email,
        'user_name': reset_password_token.user.get_full_name() or reset_password_token.user.email,
        'role': reset_password_token.user.get_role_display()
    }

    html_message = render_to_string("backend/email.html", context=context)
    plain_message = strip_tags(html_message)

    msg = EmailMultiAlternatives(
        subject="Réinitialisation de votre mot de passe - {title}".format(
            title=reset_password_token.user.get_full_name() or reset_password_token.user.email),
        body=plain_message,
        from_email="codelivecamp@gmail.com",
        to=[reset_password_token.user.email]
    )

    msg.attach_alternative(html_message, "text/html")
    msg.send()
