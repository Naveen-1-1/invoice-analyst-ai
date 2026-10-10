"""payments and books: items can wait outside a run; entries record our company and approval

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10 19:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATES = ("EXTRACTED", "CHECKED", "MATCHED", "AUTO_APPROVED", "NEEDS_REVIEW", "APPROVED", "APPROVED_PARTIAL",
              "REJECTED", "ON_HOLD", "POSTED", "SCHEDULED", "PAID")
NEW_STATES = OLD_STATES[:9]


def _states(values: tuple[str, ...]) -> str:
    return "state IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_unique_constraint(op.f('uq_payment_runs_workspace_id_run_date'), 'payment_runs', ['workspace_id', 'run_date'])
    op.alter_column('payment_items', 'run_id', nullable=True)
    op.add_column('payment_items', sa.Column('approval_id', sa.BigInteger(), nullable=True))
    op.add_column('payment_items', sa.Column('vendor_id', sa.BigInteger(), nullable=True))
    op.add_column('payment_items', sa.Column('status', sa.Text(), nullable=False, server_default='planned'))
    op.add_column('payment_items', sa.Column('due_date', sa.Date(), nullable=True))
    op.add_column('payment_items', sa.Column('due_source', sa.Text(), nullable=True))
    op.create_foreign_key(op.f('fk_payment_items_approval_id_approvals'), 'payment_items', 'approvals', ['approval_id'],
                          ['id'], ondelete='CASCADE')
    op.create_unique_constraint(op.f('uq_payment_items_approval_id'), 'payment_items', ['approval_id'])
    op.create_foreign_key(op.f('fk_payment_items_vendor_id_vendors'), 'payment_items', 'vendors', ['vendor_id'], ['id'],
                          ondelete='SET NULL')
    op.create_index(op.f('ix_payment_items_vendor_id'), 'payment_items', ['vendor_id'])
    op.create_check_constraint(op.f('ck_payment_items_status'), 'payment_items', "status IN ('planned', 'waiting', 'paid')")

    op.add_column('journal_entries', sa.Column('entity_id', sa.BigInteger(), nullable=True))
    op.add_column('journal_entries', sa.Column('approval_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_journal_entries_entity_id_entities'), 'journal_entries', 'entities', ['entity_id'], ['id'])
    op.create_foreign_key(op.f('fk_journal_entries_approval_id_approvals'), 'journal_entries', 'approvals', ['approval_id'],
                          ['id'])
    op.create_index(op.f('ix_journal_entries_entity_id'), 'journal_entries', ['entity_id'])

    op.add_column('vendor_bank_accounts', sa.Column('source', sa.Text(), nullable=True))
    op.execute("UPDATE vendor_bank_accounts SET source = 'companion'")

    op.drop_constraint(op.f('ck_invoices_state'), 'invoices', type_='check')
    op.create_check_constraint(op.f('ck_invoices_state'), 'invoices', _states(NEW_STATES))


def downgrade() -> None:
    op.drop_constraint(op.f('ck_invoices_state'), 'invoices', type_='check')
    op.create_check_constraint(op.f('ck_invoices_state'), 'invoices', _states(OLD_STATES))
    op.drop_column('vendor_bank_accounts', 'source')
    op.drop_index(op.f('ix_journal_entries_entity_id'), 'journal_entries')
    op.drop_constraint(op.f('fk_journal_entries_approval_id_approvals'), 'journal_entries', type_='foreignkey')
    op.drop_constraint(op.f('fk_journal_entries_entity_id_entities'), 'journal_entries', type_='foreignkey')
    op.drop_column('journal_entries', 'approval_id')
    op.drop_column('journal_entries', 'entity_id')
    op.drop_constraint(op.f('ck_payment_items_status'), 'payment_items', type_='check')
    op.drop_index(op.f('ix_payment_items_vendor_id'), 'payment_items')
    op.drop_constraint(op.f('fk_payment_items_vendor_id_vendors'), 'payment_items', type_='foreignkey')
    op.drop_constraint(op.f('uq_payment_items_approval_id'), 'payment_items', type_='unique')
    op.drop_constraint(op.f('fk_payment_items_approval_id_approvals'), 'payment_items', type_='foreignkey')
    for col in ('due_source', 'due_date', 'status', 'vendor_id', 'approval_id'):
        op.drop_column('payment_items', col)
    op.execute("DELETE FROM payment_items WHERE run_id IS NULL")
    op.alter_column('payment_items', 'run_id', nullable=False)
    op.drop_constraint(op.f('uq_payment_runs_workspace_id_run_date'), 'payment_runs', type_='unique')
