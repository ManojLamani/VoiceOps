"""Initial schema: the tables the app created with create_all before migrations were added.

Revision ID: 0001
Revises: 
Create Date: 2026-10-13 23:11:04.176403
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_analytics',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('date', sa.DateTime(), nullable=False),
    sa.Column('total_conversations', sa.Integer(), nullable=True),
    sa.Column('resolved_conversations', sa.Integer(), nullable=True),
    sa.Column('escalated_conversations', sa.Integer(), nullable=True),
    sa.Column('avg_response_time_ms', sa.Float(), nullable=True),
    sa.Column('avg_duration_seconds', sa.Float(), nullable=True),
    sa.Column('total_tool_calls', sa.Integer(), nullable=True),
    sa.Column('successful_tool_calls', sa.Integer(), nullable=True),
    sa.Column('intents', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_analytics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_analytics_date'), ['date'], unique=False)

    op.create_table('customers',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('customer_id', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('account_status', sa.Enum('ACTIVE', 'SUSPENDED', 'CLOSED', name='accountstatus'), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customers_customer_id'), ['customer_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_customers_email'), ['email'], unique=True)

    op.create_table('knowledge_documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=500), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), nullable=True),
    sa.Column('last_indexed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('conversations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('customer_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'COMPLETED', 'ESCALATED', 'ABANDONED', name='conversationstatus'), nullable=True),
    sa.Column('primary_intent', sa.String(length=100), nullable=True),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('sentiment', sa.String(length=50), nullable=True),
    sa.Column('summary_details', sa.JSON(), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('ended_at', sa.DateTime(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.create_index('ix_conversations_user_status', ['user_id', 'status'], unique=False)

    op.create_table('knowledge_chunks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding_id', sa.String(length=100), nullable=True),
    sa.Column('token_count', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.create_index('ix_chunks_document', ['document_id', 'chunk_index'], unique=False)

    op.create_table('orders',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=50), nullable=False),
    sa.Column('customer_id', sa.String(length=36), nullable=False),
    sa.Column('products', sa.JSON(), nullable=False),
    sa.Column('total_amount', sa.Float(), nullable=False),
    sa.Column('order_date', sa.DateTime(), nullable=False),
    sa.Column('estimated_delivery', sa.DateTime(), nullable=True),
    sa.Column('actual_delivery', sa.DateTime(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'CONFIRMED', 'PROCESSING', 'SHIPPED', 'IN_TRANSIT', 'DELIVERED', 'CANCELLED', 'RETURN_REQUESTED', 'RETURNED', 'REFUNDED', name='orderstatus'), nullable=True),
    sa.Column('shipping_address', sa.Text(), nullable=True),
    sa.Column('tracking_number', sa.String(length=100), nullable=True),
    sa.Column('cancellation_eligible', sa.Boolean(), nullable=True),
    sa.Column('return_eligible', sa.Boolean(), nullable=True),
    sa.Column('return_deadline', sa.DateTime(), nullable=True),
    sa.Column('payment_method', sa.String(length=50), nullable=True),
    sa.Column('payment_status', sa.String(length=50), nullable=True),
    sa.Column('refund_amount', sa.Float(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index('ix_orders_customer_status', ['customer_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_order_id'), ['order_id'], unique=True)

    op.create_table('escalations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('customer_id', sa.String(length=36), nullable=True),
    sa.Column('ticket_id', sa.String(length=50), nullable=True),
    sa.Column('reason', sa.Enum('CUSTOMER_REQUEST', 'LOW_CONFIDENCE', 'REPEATED_FAILURE', 'SENSITIVE_ISSUE', 'OUTSIDE_SCOPE', 'CUSTOMER_DISSATISFIED', 'POLICY_REQUIRED', name='escalationreason'), nullable=False),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('customer_summary', sa.Text(), nullable=True),
    sa.Column('conversation_summary', sa.Text(), nullable=True),
    sa.Column('tools_used', sa.JSON(), nullable=True),
    sa.Column('assigned_to', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.Enum('USER', 'ASSISTANT', 'SYSTEM', 'TOOL', name='messagerole'), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('intent', sa.String(length=100), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('audio_url', sa.String(length=500), nullable=True),
    sa.Column('rag_sources', sa.JSON(), nullable=True),
    sa.Column('tool_calls', sa.JSON(), nullable=True),
    sa.Column('processing_time_ms', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index('ix_messages_conversation_created', ['conversation_id', 'created_at'], unique=False)

    op.create_table('tickets',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('ticket_id', sa.String(length=50), nullable=False),
    sa.Column('customer_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=True),
    sa.Column('subject', sa.String(length=500), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('status', sa.Enum('OPEN', 'IN_PROGRESS', 'WAITING_CUSTOMER', 'ESCALATED', 'RESOLVED', 'CLOSED', name='ticketstatus'), nullable=True),
    sa.Column('priority', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'URGENT', name='ticketpriority'), nullable=True),
    sa.Column('assigned_to', sa.String(length=36), nullable=True),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tickets_ticket_id'), ['ticket_id'], unique=True)

    op.create_table('tool_executions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=True),
    sa.Column('tool_name', sa.String(length=100), nullable=False),
    sa.Column('tool_args', sa.JSON(), nullable=False),
    sa.Column('tool_result', sa.JSON(), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('execution_time_ms', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('tool_executions')
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tickets_ticket_id'))

    op.drop_table('tickets')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_conversation_created')

    op.drop_table('messages')
    op.drop_table('escalations')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_order_id'))
        batch_op.drop_index('ix_orders_customer_status')

    op.drop_table('orders')
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.drop_index('ix_chunks_document')

    op.drop_table('knowledge_chunks')
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index('ix_conversations_user_status')

    op.drop_table('conversations')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    op.drop_table('knowledge_documents')
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customers_email'))
        batch_op.drop_index(batch_op.f('ix_customers_customer_id'))

    op.drop_table('customers')
    with op.batch_alter_table('agent_analytics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_analytics_date'))

    op.drop_table('agent_analytics')

    # PostgreSQL keeps enum types after their tables are dropped
    for name in ('accountstatus', 'conversationstatus', 'orderstatus', 'escalationreason', 'messagerole',
                 'ticketstatus', 'ticketpriority'):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
