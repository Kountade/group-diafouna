from decimal import Decimal
from django.db import models
from django.db.models import Sum, Count, Avg, Q
from django.utils import timezone
from datetime import timedelta, datetime
from collections import defaultdict
from typing import Dict, List, Any, Optional
import json

from finance.models import Account, Transaction, Partner, WithdrawalRecipient
from django.contrib.auth import get_user_model

User = get_user_model()


class DashboardService:
    """Service principal pour les données du tableau de bord"""
    
    @staticmethod
    def get_global_stats(date_range: str = 'last_30_days') -> Dict[str, Any]:
        """Obtenir les statistiques globales"""
        start_date = DashboardService._get_date_range(date_range)
        
        # Statistiques de base
        total_partners = Partner.objects.count()
        total_agents = User.objects.filter(role='agent').count()
        active_agents = User.objects.filter(role='agent', is_active=True).count()
        
        # Comptes
        global_account = Account.objects.filter(account_type='global').first()
        global_balance = global_account.balance if global_account else Decimal('0.00')
        
        partner_accounts = Account.objects.filter(account_type='partner')
        total_partner_balance = sum(acc.balance for acc in partner_accounts)
        
        agent_accounts = Account.objects.filter(account_type='agent')
        total_agent_balance = sum(acc.balance for acc in agent_accounts)
        
        # Transactions
        transactions = Transaction.objects.filter(created_at__gte=start_date)
        
        total_transactions = transactions.count()
        total_amount = transactions.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        deposits = transactions.filter(transaction_type='deposit')
        withdrawals = transactions.filter(transaction_type='withdrawal')
        transfers = transactions.filter(transaction_type='transfer_to_agent')
        
        return {
            'partners': {
                'total': total_partners,
                'total_balance': float(total_partner_balance),
                'avg_balance': float(total_partner_balance / total_partners) if total_partners > 0 else 0
            },
            'agents': {
                'total': total_agents,
                'active': active_agents,
                'total_balance': float(total_agent_balance),
                'avg_balance': float(total_agent_balance / total_agents) if total_agents > 0 else 0
            },
            'global_account': {
                'balance': float(global_balance)
            },
            'transactions': {
                'total': total_transactions,
                'total_amount': float(total_amount),
                'deposits': deposits.count(),
                'deposits_amount': float(deposits.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'withdrawals': withdrawals.count(),
                'withdrawals_amount': float(withdrawals.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'transfers': transfers.count(),
                'transfers_amount': float(transfers.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'avg_amount': float(total_amount / total_transactions) if total_transactions > 0 else 0
            },
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': timezone.now().isoformat(),
                'days': (timezone.now() - start_date).days
            }
        }
    
    @staticmethod
    def get_trends(date_range: str = 'last_30_days') -> Dict[str, Any]:
        """Obtenir les tendances et évolutions"""
        start_date = DashboardService._get_date_range(date_range)
        transactions = Transaction.objects.filter(created_at__gte=start_date)
        
        # Tendance par jour
        daily_trends = defaultdict(lambda: {
            'transactions': 0,
            'amount': Decimal('0.00'),
            'deposits': 0,
            'withdrawals': 0,
            'transfers': 0
        })
        
        for transaction in transactions:
            day_key = transaction.created_at.strftime('%Y-%m-%d')
            daily_trends[day_key]['transactions'] += 1
            daily_trends[day_key]['amount'] += transaction.amount
            
            if transaction.transaction_type == 'deposit':
                daily_trends[day_key]['deposits'] += 1
            elif transaction.transaction_type == 'withdrawal':
                daily_trends[day_key]['withdrawals'] += 1
            elif transaction.transaction_type == 'transfer_to_agent':
                daily_trends[day_key]['transfers'] += 1
        
        # Calculer les moyennes mobiles
        dates = sorted(daily_trends.keys())
        daily_data = []
        moving_avg = []
        
        for i, date in enumerate(dates):
            data = daily_trends[date]
            daily_data.append({
                'date': date,
                'transactions': data['transactions'],
                'amount': float(data['amount']),
                'deposits': data['deposits'],
                'withdrawals': data['withdrawals'],
                'transfers': data['transfers']
            })
            
            # Moyenne mobile sur 7 jours
            if i >= 6:
                last_7_days = [daily_trends[dates[j]]['amount'] for j in range(i-6, i+1)]
                moving_avg.append({
                    'date': date,
                    'average': float(sum(last_7_days) / len(last_7_days))
                })
        
        return {
            'daily': daily_data,
            'moving_average_7d': moving_avg,
            'total_days': len(dates)
        }
    
    @staticmethod
    def get_top_performers(limit: int = 10) -> Dict[str, Any]:
        """Obtenir les meilleurs performeurs"""
        # Top partenaires par volume
        top_partners = []
        for partner in Partner.objects.all():
            partner_account = Account.objects.filter(partner=partner, account_type='partner').first()
            if partner_account:
                transactions = Transaction.objects.filter(
                    Q(from_account=partner_account) | Q(to_account=partner_account)
                )
                volume = transactions.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
                count = transactions.count()
                
                top_partners.append({
                    'id': partner.id,
                    'name': partner.name,
                    'balance': float(partner_account.balance),
                    'volume': float(volume),
                    'transaction_count': count,
                    'avg_transaction': float(volume / count) if count > 0 else 0
                })
        
        top_partners = sorted(top_partners, key=lambda x: x['volume'], reverse=True)[:limit]
        
        # Top agents par volume
        top_agents = []
        agents = User.objects.filter(role='agent')
        for agent in agents:
            agent_account = Account.objects.filter(user=agent, account_type='agent').first()
            if agent_account:
                transactions = Transaction.objects.filter(
                    Q(from_account=agent_account) | Q(to_account=agent_account)
                )
                volume = transactions.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
                count = transactions.count()
                
                top_agents.append({
                    'id': agent.id,
                    'name': agent.get_full_name() or agent.email,
                    'email': agent.email,
                    'balance': float(agent_account.balance),
                    'volume': float(volume),
                    'transaction_count': count,
                    'avg_transaction': float(volume / count) if count > 0 else 0
                })
        
        top_agents = sorted(top_agents, key=lambda x: x['volume'], reverse=True)[:limit]
        
        return {
            'top_partners': top_partners,
            'top_agents': top_agents
        }
    
    @staticmethod
    def get_agent_performance(agent_id: int, date_range: str = 'last_30_days') -> Dict[str, Any]:
        """Obtenir les performances d'un agent spécifique"""
        try:
            agent = User.objects.get(id=agent_id, role='agent')
        except User.DoesNotExist:
            return {'error': 'Agent non trouvé'}
        
        start_date = DashboardService._get_date_range(date_range)
        agent_account = Account.objects.filter(user=agent, account_type='agent').first()
        
        if not agent_account:
            return {'error': 'Compte agent non trouvé'}
        
        transactions = Transaction.objects.filter(
            Q(from_account=agent_account) | Q(to_account=agent_account),
            created_at__gte=start_date
        )
        
        # Statistiques par type
        incoming = transactions.filter(to_account=agent_account)
        outgoing = transactions.filter(from_account=agent_account)
        
        withdrawals = transactions.filter(
            transaction_type='withdrawal',
            from_account=agent_account
        )
        
        transfers_received = transactions.filter(
            transaction_type='transfer_between_agents',
            to_account=agent_account
        )
        
        transfers_sent = transactions.filter(
            transaction_type='transfer_between_agents',
            from_account=agent_account
        )
        
        return {
            'agent': {
                'id': agent.id,
                'name': agent.get_full_name() or agent.email,
                'email': agent.email,
                'balance': float(agent_account.balance)
            },
            'performance': {
                'total_transactions': transactions.count(),
                'total_volume_incoming': float(incoming.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'total_volume_outgoing': float(outgoing.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'withdrawals': withdrawals.count(),
                'withdrawals_amount': float(withdrawals.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'transfers_received': transfers_received.count(),
                'transfers_received_amount': float(transfers_received.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'transfers_sent': transfers_sent.count(),
                'transfers_sent_amount': float(transfers_sent.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')),
                'avg_transaction_amount': float(transactions.aggregate(Avg('amount'))['amount__avg'] or Decimal('0.00'))
            },
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': timezone.now().isoformat()
            }
        }
    
    @staticmethod
    def get_withdrawal_analytics(date_range: str = 'last_30_days') -> Dict[str, Any]:
        """Analyses des retraits"""
        start_date = DashboardService._get_date_range(date_range)
        withdrawals = Transaction.objects.filter(
            transaction_type='withdrawal',
            created_at__gte=start_date
        )
        
        total_withdrawals = withdrawals.count()
        total_amount = withdrawals.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
        
        # Top bénéficiaires
        top_recipients = (
            WithdrawalRecipient.objects
            .filter(transactions__in=withdrawals)
            .annotate(
                total_amount=Sum('transactions__amount'),
                count=Count('transactions')
            )
            .order_by('-total_amount')[:10]
        )
        
        recipients_data = []
        for recipient in top_recipients:
            recipients_data.append({
                'id': recipient.id,
                'name': recipient.full_name,
                'phone': recipient.phone,
                'count': recipient.count,
                'total_amount': float(recipient.total_amount or 0),
                'avg_amount': float((recipient.total_amount or 0) / recipient.count) if recipient.count > 0 else 0
            })
        
        # Distribution des montants
        amount_ranges = {
            '0-1000': 0,
            '1000-5000': 0,
            '5000-10000': 0,
            '10000-50000': 0,
            '50000+': 0
        }
        
        for w in withdrawals:
            amount = float(w.amount)
            if amount <= 1000:
                amount_ranges['0-1000'] += 1
            elif amount <= 5000:
                amount_ranges['1000-5000'] += 1
            elif amount <= 10000:
                amount_ranges['5000-10000'] += 1
            elif amount <= 50000:
                amount_ranges['10000-50000'] += 1
            else:
                amount_ranges['50000+'] += 1
        
        return {
            'total_withdrawals': total_withdrawals,
            'total_amount': float(total_amount),
            'avg_amount': float(total_amount / total_withdrawals) if total_withdrawals > 0 else 0,
            'top_recipients': recipients_data,
            'amount_distribution': amount_ranges,
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': timezone.now().isoformat()
            }
        }
    
    @staticmethod
    def get_balance_snapshot() -> Dict[str, Any]:
        """Instantané des soldes"""
        # Comptes partenaires
        partner_accounts = Account.objects.filter(account_type='partner')
        partner_data = []
        total_partner_balance = Decimal('0.00')
        
        for acc in partner_accounts:
            if acc.partner:
                partner_data.append({
                    'name': acc.partner.name,
                    'balance': float(acc.balance),
                    'currency': acc.currency
                })
                total_partner_balance += acc.balance
        
        # Comptes agents
        agent_accounts = Account.objects.filter(account_type='agent')
        agent_data = []
        total_agent_balance = Decimal('0.00')
        
        for acc in agent_accounts:
            if acc.user:
                agent_data.append({
                    'name': acc.user.get_full_name() or acc.user.email,
                    'email': acc.user.email,
                    'balance': float(acc.balance),
                    'currency': acc.currency
                })
                total_agent_balance += acc.balance
        
        # Top soldes
        top_partners = sorted(partner_data, key=lambda x: x['balance'], reverse=True)[:10]
        top_agents = sorted(agent_data, key=lambda x: x['balance'], reverse=True)[:10]
        
        return {
            'partner_accounts': {
                'total': len(partner_data),
                'total_balance': float(total_partner_balance),
                'top_balances': top_partners,
                'avg_balance': float(total_partner_balance / len(partner_data)) if partner_data else 0,
                'max_balance': float(top_partners[0]['balance']) if top_partners else 0,
                'min_balance': float(top_partners[-1]['balance']) if top_partners else 0
            },
            'agent_accounts': {
                'total': len(agent_data),
                'total_balance': float(total_agent_balance),
                'top_balances': top_agents,
                'avg_balance': float(total_agent_balance / len(agent_data)) if agent_data else 0,
                'max_balance': float(top_agents[0]['balance']) if top_agents else 0,
                'min_balance': float(top_agents[-1]['balance']) if top_agents else 0
            }
        }
    
    @staticmethod
    def get_alerts(user_id: Optional[int] = None) -> Dict[str, Any]:
        """Obtenir les alertes actives"""
        from .models import DashboardAlert
        
        alerts = DashboardAlert.objects.filter(is_active=True, is_read=False)
        
        if user_id:
            alerts = alerts.filter(Q(user_id=user_id) | Q(user__isnull=True))
        else:
            alerts = alerts.filter(user__isnull=True)
        
        alerts_data = []
        for alert in alerts:
            alerts_data.append({
                'id': alert.id,
                'title': alert.title,
                'message': alert.message,
                'type': alert.alert_type,
                'created_at': alert.created_at.isoformat(),
                'expires_at': alert.expires_at.isoformat() if alert.expires_at else None
            })
        
        # Créer des alertes automatiques si nécessaire
        DashboardService._generate_auto_alerts()
        
        return {
            'alerts': alerts_data,
            'total_unread': alerts.count()
        }
    
    @staticmethod
    def _generate_auto_alerts():
        """Générer des alertes automatiques"""
        from .models import DashboardAlert
        
        # Vérifier les soldes bas
        for account in Account.objects.filter(account_type='partner'):
            if account.balance < 10000:
                DashboardAlert.objects.get_or_create(
                    title=f"Solde bas - {account.partner.name if account.partner else 'Partenaire'}",
                    defaults={
                        'message': f"Le solde du compte est de {account.balance} {account.currency}. Attention, le seuil minimum est de 10.000.",
                        'alert_type': 'warning',
                        'is_active': True
                    }
                )
        
        # Vérifier les soldes agents
        for account in Account.objects.filter(account_type='agent'):
            if account.balance < 1000:
                DashboardAlert.objects.get_or_create(
                    title=f"Solde bas - {account.user.email if account.user else 'Agent'}",
                    defaults={
                        'message': f"Le solde du compte est de {account.balance} {account.currency}. Attention, le seuil minimum est de 1.000.",
                        'alert_type': 'warning',
                        'is_active': True
                    }
                )
    
    @staticmethod
    def _get_date_range(range_type: str) -> datetime:
        """Obtenir la date de début selon le type de plage"""
        now = timezone.now()
        
        ranges = {
            'today': now - timedelta(days=1),
            'yesterday': now - timedelta(days=2),
            'last_7_days': now - timedelta(days=7),
            'last_30_days': now - timedelta(days=30),
            'last_90_days': now - timedelta(days=90),
            'last_180_days': now - timedelta(days=180),
            'last_year': now - timedelta(days=365),
            'all_time': now - timedelta(days=3650),  # 10 ans
            'this_week': now - timedelta(days=now.weekday()),
            'this_month': now.replace(day=1),
            'this_quarter': now.replace(month=((now.month - 1) // 3) * 3 + 1, day=1),
        }
        
        return ranges.get(range_type, ranges['last_30_days'])