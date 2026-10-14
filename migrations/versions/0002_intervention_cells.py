"""intervention cells — one topic may serve several subdomains

Revision ID: 0002_intervention_cells
Revises: 0001_initial_schema
Create Date: 2026-10-14

0001 put `domain_code` and `subdomain_code` on `intervention_catalogue`
alongside a UNIQUE `intervention_code`. That shape cannot represent one topic
serving two cells, and the supplied draft curriculum already contains one:

    PO-EPS-T01  "Goal Clarity and Alignment"
        -> (GEM, 'M')  Match with Overall Expectations
        -> (TID, 'I')  Purpose and Impact

which is correct content, not a data error. The same intervention legitimately
answers two findings.

Keeping the columns would have forced a choice between duplicating the topic
under two codes — and then deduplicating it by title in every report, forever —
or dropping one mapping and silently losing a recommendation. The code names
the TOPIC; the mapping names the CELL IT SERVES; they are different things.

Caught before any catalogue row existed, so this moves no data.
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_intervention_cells'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'intervention_cells',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('intervention_id', sa.UUID(), nullable=False),
        sa.Column('domain_code', sa.String(length=3), nullable=False),
        sa.Column('subdomain_code', sa.String(length=1), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['intervention_id'], ['intervention_catalogue.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('intervention_id', 'domain_code', 'subdomain_code',
                            name='uq_intervention_cell'),
    )
    op.create_index(op.f('ix_intervention_cells_intervention_id'),
                    'intervention_cells', ['intervention_id'], unique=False)
    op.create_index('ix_cell_lookup', 'intervention_cells',
                    ['domain_code', 'subdomain_code'], unique=False)

    op.drop_index('ix_catalogue_cell', table_name='intervention_catalogue')
    op.drop_column('intervention_catalogue', 'subdomain_code')
    op.drop_column('intervention_catalogue', 'domain_code')


def downgrade() -> None:
    # The columns come back NULLABLE. They were NOT NULL in 0001, but a
    # downgrade cannot invent a single cell for a topic that serves two —
    # which is the whole reason this revision exists. Reversing it on a
    # populated catalogue loses mappings; that is a property of the old shape,
    # not of this migration.
    op.add_column('intervention_catalogue',
                  sa.Column('domain_code', sa.String(length=3), nullable=True))
    op.add_column('intervention_catalogue',
                  sa.Column('subdomain_code', sa.String(length=1), nullable=True))
    op.create_index('ix_catalogue_cell', 'intervention_catalogue',
                    ['domain_code', 'subdomain_code', 'is_active'], unique=False)

    op.drop_index('ix_cell_lookup', table_name='intervention_cells')
    op.drop_index(op.f('ix_intervention_cells_intervention_id'),
                  table_name='intervention_cells')
    op.drop_table('intervention_cells')
