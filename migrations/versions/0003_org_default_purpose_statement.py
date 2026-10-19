"""orgs.default_purpose_statement — the §9.2 statement, written once per org

Revision ID: 0003_org_default_purpose
Revises: 0002_intervention_cells
Create Date: 2026-10-19

The cycle creation form asked an L2 admin for eight fields, six of which have a
correct default and one of which — the purpose statement — is an ORGANISATION's
standing commitment that was being re-typed, or more realistically re-pasted,
every cycle. Re-typing a consent statement is how it degrades into "Survey".

`measurement_cycles.purpose_statement` is UNCHANGED and still NOT NULL: every
cycle still carries its own text and the survey landing page still renders that
copy, so §9.2 remains a property of the schema. This column only supplies what a
NEW cycle is created with. An L3 admin creating a branch cycle inherits the
organisation's wording instead of inventing a weaker one, and either tier may
edit a cycle's own copy afterwards without touching this default.

Nullable, because every existing org predates it. Blank is the prompt state —
the L2 dashboard carries a reminder until it is filled and cycle creation
refuses while it is empty.

This moves no data and drops none: downgrade removes a column that nothing else
references.
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_org_default_purpose'
down_revision = '0002_intervention_cells'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'orgs',
        sa.Column('default_purpose_statement', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('orgs', 'default_purpose_statement')
