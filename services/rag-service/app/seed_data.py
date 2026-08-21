"""Seed knowledge base for a fictional SaaS help-desk product, "Onwly".

This is intentionally generic/fictional content (not real production company
product docs) so /query has something real to retrieve against immediately after
`docker compose up`, without depending on any external data source.
"""
from typing import Any, Dict, List

SEED_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "doc_id": "doc_getting_started",
        "source": "help-center/getting-started",
        "text": """
Getting Started with Onwly

Onwly is a customer support and ticketing platform for small and mid-sized
teams. This guide walks you through setting up your workspace for the first
time.

Step 1: Create your workspace. When you sign up, you choose a workspace name
and a subdomain (for example, acme.onwly.io). Every user in your
organization will use this subdomain to log in.

Step 2: Invite your team. From Settings > Team, click "Invite members" and
enter email addresses. Invited users receive an email with a link to set
their password. You can invite up to 5 members on the Free plan; paid plans
support unlimited members.

Step 3: Connect your support channels. Onwly can receive tickets from
email, a web widget embedded on your site, and Slack. Go to Settings >
Channels to connect each one. Most teams start with email forwarding: you
set up a forwarding rule from your existing support@yourcompany.com address
to the unique Onwly ingestion address shown on that page.

Step 4: Set up your first automation. Automations let you route tickets to
the right team automatically. For example, you can route any ticket
containing the word "refund" to your Billing team. We cover automations in
more depth in a separate guide.

Step 5: Invite a few real customers or send yourself a test email to see a
ticket appear in your queue. New workspaces come pre-loaded with three
sample tickets so your queue isn't empty on day one — feel free to delete
these once you're comfortable with the interface.

If you get stuck at any point, click the "?" icon in the bottom-right corner
of any page to open the in-app help widget, or email onboarding@onwly.io
and a real person will get back to you within one business day.
""".strip(),
    },
    {
        "doc_id": "doc_billing",
        "source": "help-center/billing-and-plans",
        "text": """
Billing and Subscription Plans

Onwly offers three plans: Free, Team, and Business.

The Free plan supports up to 5 team members and 100 tickets per month, with
email and web-widget channels only. It does not include automations,
reporting, or API access.

The Team plan is $29 per agent per month, billed monthly or annually (annual
billing gets a 20% discount). It includes unlimited tickets, all channels
including Slack, automations, and basic reporting.

The Business plan is $59 per agent per month and adds SSO/SAML login,
advanced reporting with CSV export, a full REST API, and priority support
with a 4-hour response SLA.

Changing plans: You can upgrade or downgrade at any time from Settings >
Billing > Change plan. Upgrades take effect immediately and you're charged a
prorated amount for the rest of the current billing cycle. Downgrades take
effect at the start of your next billing cycle — you keep your current
plan's features until then.

Payment methods: We accept all major credit cards and, on annual Business
plans, invoicing with net-30 terms. To switch from credit card to invoicing,
contact billing@onwly.io.

Refunds: If you're on a monthly plan and cancel within the first 14 days of
your very first subscription, we'll refund that month in full — email
billing@onwly.io with your workspace subdomain. We don't offer prorated
refunds for canceling mid-cycle on months after the first.

Failed payments: If a card payment fails, we retry it three times over seven
days. If all retries fail, the workspace is downgraded to the Free plan
(your data is not deleted) and the account owner receives an email with a
link to update payment details and restore the paid plan.
""".strip(),
    },
    {
        "doc_id": "doc_login_troubleshooting",
        "source": "help-center/troubleshooting-login",
        "text": """
Troubleshooting Login and Password Issues

"I forgot my password." On the login page, click "Forgot password?" and
enter the email address associated with your account. You'll receive a
password reset link that's valid for 60 minutes. If you don't see the email
within a few minutes, check your spam folder, and make sure you're checking
the inbox for the email you actually signed up with — many people have more
than one.

"My reset link expired or says invalid." Reset links are single-use and
expire after 60 minutes. Just request a new one from the "Forgot password?"
page — there's no limit on how many times you can request a reset link.

"I'm locked out after too many failed attempts." For security, Onwly
locks an account for 15 minutes after 5 consecutive failed login attempts.
Wait 15 minutes and try again, or use the "Forgot password?" flow, which
also clears the lockout.

"My company uses SSO and I can't find the password option." If your
workspace has SSO/SAML enabled (Business plan only), password login is
disabled workspace-wide and everyone must sign in through your identity
provider (Okta, Azure AD, Google Workspace, etc.). Contact your internal IT
team, not Onwly support, if SSO itself is failing — Onwly cannot
reset an SSO-managed identity.

"I can log in but I don't see my workspace, or I see the wrong workspace."
Onwly accounts are scoped to an email address across all workspaces you
belong to. After logging in, use the workspace switcher in the top-left
corner to move between workspaces. If a workspace you expect isn't listed,
ask an admin of that workspace to re-invite the email address you're using.

Still stuck? Email support@onwly.io from the email address on the
account (this helps us verify it's really you) and include your workspace
subdomain.
""".strip(),
    },
    {
        "doc_id": "doc_data_export",
        "source": "help-center/exporting-your-data",
        "text": """
Exporting Your Data

Onwly gives you several ways to get your data out, because we believe
you should never feel locked in.

CSV export of tickets: Available on Team and Business plans. From the
Tickets view, apply any filters you want (date range, status, assigned
team), then click Export > CSV in the top-right toolbar. The export runs in
the background and emails you a download link when it's ready — this
usually takes under a minute for a few thousand tickets, but larger exports
(50,000+ tickets) can take up to 30 minutes.

Full account export: Business plan only. Settings > Data > Export account
data generates a ZIP archive containing every ticket, customer profile,
attachment, and automation rule in your workspace as JSON files, plus
attachments in their original format. This is the same format used for
Onwly-to-Onwly workspace migrations.

API access: Business plan customers can also pull data programmatically via
the REST API (see the Ticket Management API guide) rather than waiting for a
batch export — useful if you want to sync tickets into your own data
warehouse on a schedule.

Data retention after cancellation: If you cancel your subscription, your
workspace data is retained in read-only mode for 90 days, during which you
can still export it. After 90 days it is permanently deleted and cannot be
recovered, so make sure to export anything you need before then.

Exporting a single customer's data (for GDPR/privacy requests): Go to a
customer's profile page and click "Export customer data" to generate a
single-customer JSON export containing every ticket and message associated
with that customer's email address. Use "Delete customer data" on the same
page to permanently erase it, which is irreversible.
""".strip(),
    },
    {
        "doc_id": "doc_integrations",
        "source": "help-center/integrations",
        "text": """
Connecting Integrations: Slack, Email, and Zapier

Slack integration: Go to Settings > Channels > Slack and click "Connect to
Slack." You'll be redirected to Slack to authorize Onwly, then asked to
choose a channel (for example #support) where new ticket notifications will
post. Agents can also reply to tickets directly from Slack using the
"/onwly reply" slash command, and resolve a ticket with "/onwly
resolve". Note that Slack notifications are one-way for the Free and Team
plans (Slack shows you the ticket, but you still reply in Onwly) —
two-way Slack replies require the Business plan.

Email forwarding: Set up a forwarding rule in your existing email provider
(Gmail, Outlook, etc.) from your support address to the unique ingestion
address shown in Settings > Channels > Email. Every forwarded email becomes
a new ticket, and every reply sent from that customer's original email
address is threaded onto the existing ticket automatically as long as the
subject line's ticket reference number (e.g. [Ticket #1042]) is preserved.

Zapier: Onwly has an official Zapier app supporting triggers ("New
ticket created," "Ticket resolved," "Ticket tagged") and actions ("Create
ticket," "Add internal note," "Update ticket status"). This is the easiest
way to connect Onwly to tools without a native integration, like
Notion, Airtable, or a custom Google Sheet. Zapier is available on all
plans, but each Zap counts against your Zapier plan's task limits, not
Onwly's.

Removing an integration: Settings > Channels > [integration] > Disconnect.
Disconnecting Slack or email does not delete tickets already created through
that channel; it only stops new ones from coming in.
""".strip(),
    },
    {
        "doc_id": "doc_roles_permissions",
        "source": "help-center/team-roles-and-permissions",
        "text": """
Team Roles and Permissions

Onwly has four roles: Owner, Admin, Agent, and Viewer.

Owner: There is exactly one Owner per workspace — usually whoever created
it. The Owner can do everything Admins can, plus change billing, transfer
ownership to another team member, and delete the entire workspace. Ownership
transfer requires the new owner to accept an emailed confirmation.

Admin: Can manage team members (invite, remove, change roles below Owner),
configure channels and automations, and access all billing information
except changing the payment method. Admins cannot delete the workspace or
downgrade below the plan an Owner has committed to via annual billing.

Agent: Can view, reply to, and resolve tickets assigned to them or to teams
they belong to. Agents cannot see billing settings, cannot invite or remove
team members, and by default cannot see tickets assigned to teams they are
not a member of — this is configurable per-workspace under Settings >
Permissions > "Restrict ticket visibility to assigned team."

Viewer: Read-only access to tickets and reports. Useful for stakeholders
(e.g. a product manager) who want visibility into customer issues without
being able to reply or make changes. Viewers do not count against your
agent seat limit on paid plans, so they're free to add.

Changing a team member's role: Settings > Team > click the member > change
their role from the dropdown. Role changes take effect immediately; the
affected user does not need to log out and back in.

Teams vs. roles: "Teams" (e.g. Billing, Technical Support) are a separate
concept from roles — they're used for ticket routing and assignment, and a
single agent can belong to multiple teams. Roles control what a user can
do; teams control what they're expected to work on.
""".strip(),
    },
    {
        "doc_id": "doc_notifications",
        "source": "help-center/notification-settings",
        "text": """
Notification Settings

Onwly sends notifications through in-app alerts, email, and (if
connected) Slack. Each channel can be configured independently per user
under Settings > Notifications.

In-app notifications: On by default for "Ticket assigned to me," "New reply
on a ticket I'm following," and "@mentioned in an internal note." You can
also opt in to "New ticket in my team's queue," which is off by default to
avoid noise on busy teams.

Email digests: Instead of one email per event, most agents prefer the daily
digest, sent every morning at 8am in your workspace's configured timezone,
summarizing overnight activity. You can switch to real-time email
notifications, or turn email notifications off entirely and rely on in-app
and Slack alerts.

Slack notifications: If Slack is connected (see the Integrations guide),
you can additionally choose to receive direct Slack messages (not just
channel posts) for tickets assigned specifically to you. This is a
per-user opt-in under Settings > Notifications > Slack DMs.

Customer-facing notifications: Separate from internal team notifications,
customers automatically receive an email whenever an agent replies to their
ticket, and an email confirming resolution when a ticket is marked resolved.
These customer emails cannot be disabled per-agent — they're a workspace-wide
setting under Settings > Customer Communication, since disabling them
would mean customers stop hearing back at all.

Snoozing notifications: Click your avatar > "Pause notifications" to snooze
all notification channels for a chosen duration (1 hour, until tomorrow, or
until a custom time). Ticket assignment and SLA timers keep running while
notifications are paused — pausing only affects whether you're alerted.
""".strip(),
    },
    {
        "doc_id": "doc_api_tickets",
        "source": "help-center/ticket-management-api",
        "text": """
Ticket Management API

The Ticket Management API is available on the Business plan and lets you
create, read, update, and search tickets programmatically. The base URL is
https://api.onwly.io/v1/.

Authentication: Generate an API key from Settings > API > "Create API key."
Send it as a Bearer token in the Authorization header on every request.
API keys are scoped to the workspace that created them and inherit the
permissions of the user who generated them — an Agent's API key cannot see
billing endpoints, for example.

Creating a ticket: POST /v1/tickets with a JSON body containing `subject`,
`description`, and `customer_email`. Optional fields include `priority`
(low, normal, high, urgent), `team_id`, and `tags` (an array of strings).
A successful request returns 201 with the created ticket object, including
its numeric `id`.

Listing and filtering tickets: GET /v1/tickets supports query parameters
`status` (open, pending, resolved, closed), `team_id`, `assignee_id`, and
`created_after` / `created_before` (ISO 8601 timestamps). Results are
paginated at 50 per page by default (max 200 via `page_size`); use the
`next_cursor` field in the response to fetch subsequent pages.

Updating a ticket: PATCH /v1/tickets/{id} accepts any subset of `status`,
`priority`, `assignee_id`, and `tags`. Only fields you include are changed;
omitted fields are left as-is.

Rate limits: 300 requests per minute per API key, returned in the
`X-RateLimit-Remaining` response header. Exceeding the limit returns HTTP
429 with a `Retry-After` header indicating how many seconds to wait.

Webhooks: Rather than polling GET /v1/tickets, you can register a webhook
URL under Settings > API > Webhooks to receive a POST request whenever a
ticket is created, updated, or resolved — this is the recommended approach
for keeping an external system in sync in near-real-time.
""".strip(),
    },
]
