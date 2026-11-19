# First-run setup of a Chatwoot instance; rails runner, after db:chatwoot_prepare.
#
# Safe to run on every install and upgrade. It
# - refuses to continue without pgvector
# - switches public signup and account creation off in the database, where Chatwoot
#   reads them after the first start (the environment only seeds them once)
# - creates the account and its super admin when none exists, so the onboarding page
#   that lets the first visitor become super admin never opens
# - with CHATWOOT_PLATFORM_TOKEN: keeps the Platform App of the login bridge with exactly
#   that token and gives it the account
# - with CHATWOOT_AGENT_BOT_TOKEN: keeps the Agent Bot with that access token, webhook
#   secret and URL, connected to exactly the inboxes named in CHATWOOT_AGENT_BOT_INBOXES
# - with CHATWOOT_ODOO_BRIDGE_TOKEN: keeps the Odoo bridge's agent with that access token,
#   the API inboxes named in CHATWOOT_ODOO_INBOXES with their webhooks to the bridge, signed
#   with CHATWOOT_ODOO_INBOX_SECRET, and the conversation attributes odoo_ref and odoo_url
# - with CHATWOOT_DRAFT_TOKEN: keeps the author of AI drafts (an agent with that access
#   token, member of the inboxes in CHATWOOT_DRAFT_INBOXES) and the account webhook of new
#   messages to the bridge, signed with CHATWOOT_DRAFT_WEBHOOK_SECRET
# - with CHATWOOT_AGENT_PANEL_PATH: keeps the Dashboard App of the agent panel at that path
#   of FRONTEND_URL
# - with CHATWOOT_TRIAGE_LABELS: keeps those labels, and the teams of CHATWOOT_TRIAGE_TEAMS
#   (JSON, team name to member emails) with auto assignment; adds each listed member that
#   has a user to the team and to the CHATWOOT_DRAFT_INBOXES, so the team's conversations
#   there can be assigned to them, and switches the inboxes' own auto assignment off.
#   Members and labels added in Chatwoot stay
# It prints no credential.

conn = ActiveRecord::Base.connection
abort 'pgvector (extension vector) is not enabled in the database' unless conn.extension_enabled?('vector')

%w[ENABLE_ACCOUNT_SIGNUP CREATE_NEW_ACCOUNT_FROM_DASHBOARD].each do |name|
  config = InstallationConfig.find_or_initialize_by(name: name)
  next if config.persisted? && config.value == false

  config.value = false
  config.save!
  puts "#{name} switched off"
end
GlobalConfig.clear_cache

account_id = Integer(ENV.fetch('CHATWOOT_ACCOUNT_ID'))
account = Account.order(:id).first
if account.nil?
  _user, account = AccountBuilder.new(
    account_name: ENV.fetch('CHATWOOT_ACCOUNT_NAME'),
    email: ENV.fetch('CHATWOOT_ADMIN_EMAIL').strip.downcase,
    user_full_name: 'Administrator',
    user_password: ENV.fetch('CHATWOOT_ADMIN_PASSWORD'),
    super_admin: true,
    confirmed: true
  ).perform
  puts "account #{account.id} and its super admin created"
end
abort "the first account has id #{account.id}, the chart is set to #{account_id}" if account.id != account_id

# the page that makes the first visitor super admin
Redis::Alfred.delete(Redis::Alfred::CHATWOOT_INSTALLATION_ONBOARDING)

token = ENV.fetch('CHATWOOT_PLATFORM_TOKEN', '')
if token.empty?
  puts 'no platform token: login bridge not set up'
else
  abort 'platform token shorter than 32 characters' if token.length < 32
  app = PlatformApp.find_or_create_by!(name: ENV.fetch('CHATWOOT_PLATFORM_APP'))
  access = app.access_token || app.create_access_token
  access.reload
  unless ActiveSupport::SecurityUtils.secure_compare(access.token.to_s, token)
    access.update!(token: token)
    puts "platform app #{app.name}: token set"
  end
  app.platform_app_permissibles.find_or_create_by!(permissible: account)
  puts "platform app #{app.name} may manage account #{account.id}"
  # a super admin is never the bridge's to log in (the chart initializer refuses it too)
  dropped = app.platform_app_permissibles.where(permissible_type: 'User', permissible_id: SuperAdmin.select(:id)).delete_all
  puts "platform app #{app.name}: #{dropped} super admin permission(s) removed" if dropped.positive?
end
bot_token = ENV.fetch('CHATWOOT_AGENT_BOT_TOKEN', '')
if bot_token.empty?
  puts 'no agent bot token: agent bot not set up'
else
  secret = ENV.fetch('CHATWOOT_AGENT_BOT_WEBHOOK_SECRET')
  abort 'agent bot token or webhook secret shorter than 32 characters' if bot_token.length < 32 || secret.length < 32
  bot = AgentBot.find_or_initialize_by(account_id: account.id, name: ENV.fetch('CHATWOOT_AGENT_BOT_NAME'))
  bot.outgoing_url = ENV.fetch('CHATWOOT_AGENT_BOT_URL')
  bot.description = 'Answers customers through an A2A agent until a person takes over (set by the chart)'
  bot.secret = secret unless bot.persisted? && ActiveSupport::SecurityUtils.secure_compare(bot.secret.to_s, secret)
  bot.save!
  access = bot.access_token || bot.create_access_token
  access.reload
  access.update!(token: bot_token) unless ActiveSupport::SecurityUtils.secure_compare(access.token.to_s, bot_token)
  wanted = ENV.fetch('CHATWOOT_AGENT_BOT_INBOXES', '').split(',').map(&:strip).reject(&:empty?)
  account.inboxes.order(:id).each do |inbox|
    link = AgentBotInbox.find_by(inbox_id: inbox.id)
    if wanted.include?('*') || wanted.include?(inbox.name)
      link ||= AgentBotInbox.new(inbox: inbox)
      link.agent_bot = bot
      link.status = :active
      link.save!
      puts "agent bot #{bot.name}: inbox #{inbox.id} connected"
    elsif link && link.agent_bot_id == bot.id
      link.destroy!
      puts "agent bot #{bot.name}: inbox #{inbox.id} disconnected"
    end
  end
  missing = wanted - ['*'] - account.inboxes.pluck(:name)
  puts "agent bot #{bot.name}: no inbox named #{missing.join(', ')}" if missing.any?
end

odoo_token = ENV.fetch('CHATWOOT_ODOO_BRIDGE_TOKEN', '')
if odoo_token.empty?
  puts 'no odoo bridge token: odoo bridge not set up'
else
  secret = ENV.fetch('CHATWOOT_ODOO_INBOX_SECRET')
  abort 'odoo bridge token or inbox secret shorter than 32 characters' if odoo_token.length < 32 || secret.length < 32
  # the bridge's own agent; under .invalid no login at the OIDC provider can match it
  user = User.find_or_initialize_by(email: ENV.fetch('CHATWOOT_ODOO_BRIDGE_EMAIL'))
  if user.new_record?
    user.name = 'Odoo'
    user.password = "#{SecureRandom.base64(48)}-Aa1"
    user.confirmed_at = Time.current
    user.save!
    puts "odoo bridge user #{user.id} created"
  end
  AccountUser.find_or_create_by!(account: account, user: user) { |member| member.role = :agent }
  access = user.access_token || user.create_access_token
  access.reload
  access.update!(token: odoo_token) unless ActiveSupport::SecurityUtils.secure_compare(access.token.to_s, odoo_token)
  url = ENV.fetch('CHATWOOT_ODOO_WEBHOOK_URL')
  ENV.fetch('CHATWOOT_ODOO_INBOXES').split(',').map(&:strip).reject(&:empty?).each do |name|
    inbox = account.inboxes.find_by(name: name)
    if inbox.nil?
      inbox = account.inboxes.create!(name: name, channel: Channel::Api.create!(account: account, webhook_url: url))
      puts "odoo inbox #{name}: created"
    end
    abort "inbox #{name} is a #{inbox.channel_type}, the odoo bridge needs an API inbox" unless inbox.channel_type == 'Channel::Api'
    channel = inbox.channel
    channel.webhook_url = url
    channel.secret = secret unless ActiveSupport::SecurityUtils.secure_compare(channel.secret.to_s, secret)
    channel.save! if channel.changed?
    InboxMember.find_or_create_by!(inbox: inbox, user: user)
    puts "odoo inbox #{name} (#{inbox.id}): webhooks to the bridge"
  end
  # shown in the conversation sidebar; the bridge keys every conversation on odoo_ref
  { 'odoo_ref' => ['Odoo record', :text], 'odoo_url' => ['Odoo', :link] }.each do |key, (label, type)|
    definition = CustomAttributeDefinition.find_or_initialize_by(account: account, attribute_key: key,
                                                                 attribute_model: :conversation_attribute)
    definition.attribute_display_name = label
    definition.attribute_display_type = type
    definition.save! if definition.changed?
  end
end

draft_token = ENV.fetch('CHATWOOT_DRAFT_TOKEN', '')
if draft_token.empty?
  puts 'no draft token: AI drafts not set up'
else
  secret = ENV.fetch('CHATWOOT_DRAFT_WEBHOOK_SECRET')
  abort 'draft token or webhook secret shorter than 32 characters' if draft_token.length < 32 || secret.length < 32
  # the drafts' author; under .invalid no login at the OIDC provider can match it
  user = User.find_or_initialize_by(email: ENV.fetch('CHATWOOT_DRAFT_EMAIL'))
  user.name = ENV.fetch('CHATWOOT_DRAFT_NAME')
  if user.new_record?
    user.password = "#{SecureRandom.base64(48)}-Aa1"
    user.confirmed_at = Time.current
  end
  user.save! if user.changed?
  AccountUser.find_or_create_by!(account: account, user: user) { |member| member.role = :agent }
  access = user.access_token || user.create_access_token
  access.reload
  access.update!(token: draft_token) unless ActiveSupport::SecurityUtils.secure_compare(access.token.to_s, draft_token)
  # an agent reads and writes the conversations of its inboxes only
  wanted = ENV.fetch('CHATWOOT_DRAFT_INBOXES').split(',').map(&:strip).reject(&:empty?)
  account.inboxes.order(:id).each do |inbox|
    if wanted.include?('*') || wanted.include?(inbox.name)
      InboxMember.find_or_create_by!(inbox: inbox, user: user)
    else
      InboxMember.where(inbox: inbox, user: user).destroy_all
    end
  end
  missing = wanted - ['*'] - account.inboxes.pluck(:name)
  puts "AI drafts: no inbox named #{missing.join(', ')}" if missing.any?
  # the account webhook of new messages, signed with the draft secret
  url = ENV.fetch('CHATWOOT_DRAFT_WEBHOOK_URL')
  hook = Webhook.find_or_initialize_by(account_id: account.id, url: url)
  hook.name = 'AI drafts (set by the chart)'
  hook.webhook_type = :account_type
  hook.subscriptions = ['message_created']
  hook.secret = secret unless hook.persisted? && ActiveSupport::SecurityUtils.secure_compare(hook.secret.to_s, secret)
  hook.save! if hook.changed?
  puts "AI drafts: user #{user.id}, webhook #{hook.id}, inboxes #{wanted.join(', ')}"
end

triage_labels = ENV.fetch('CHATWOOT_TRIAGE_LABELS', '').split(',').map(&:strip).reject(&:empty?)
if triage_labels.empty?
  puts 'no triage labels: AI triage not set up'
else
  triage_labels.each do |title|
    label = Label.find_or_initialize_by(account: account, title: title.downcase)
    next if label.persisted?

    label.description = 'AI triage (set by the chart)'
    label.color = '#1f93ff' if label.color.blank?
    label.show_on_sidebar = true
    label.save!
    puts "label #{label.title} created"
  end
  wanted = ENV.fetch('CHATWOOT_DRAFT_INBOXES', '').split(',').map(&:strip).reject(&:empty?)
  inboxes = wanted.include?('*') ? account.inboxes.to_a : account.inboxes.where(name: wanted).to_a
  # Chatwoot's own round robin would give a new conversation to any online member before the
  # triage names its team; the team's auto assignment works without it
  inboxes.select(&:enable_auto_assignment).each do |inbox|
    inbox.update!(enable_auto_assignment: false)
    puts "inbox #{inbox.name}: auto assignment off, the triage assigns"
  end
  JSON.parse(ENV.fetch('CHATWOOT_TRIAGE_TEAMS', '{}')).each do |name, emails|
    team = Team.find_or_initialize_by(account: account, name: name.strip.downcase)
    if team.new_record?
      team.description = 'AI triage (set by the chart)'
      team.allow_auto_assign = true
      team.save!
      puts "team #{team.name} created"
    end
    Array(emails).each do |email|
      user = account.users.find_by(email: email.to_s.strip.downcase)
      if user.nil?
        puts "team #{team.name}: no user #{email} yet; joins at the next upgrade after a first login"
        next
      end
      TeamMember.find_or_create_by!(team: team, user: user)
      inboxes.each { |inbox| InboxMember.find_or_create_by!(inbox: inbox, user: user) }
    end
    puts "team #{team.name}: #{team.members.count} member(s)"
  end
end

panel_path = ENV.fetch('CHATWOOT_AGENT_PANEL_PATH', '')
if panel_path.empty?
  puts 'no agent panel path: agent panel not set up'
else
  panel_url = ENV.fetch('FRONTEND_URL').chomp('/') + panel_path
  title = ENV.fetch('CHATWOOT_AGENT_PANEL_TITLE')
  app = DashboardApp.find_or_initialize_by(account_id: account.id, title: title)
  app.user ||= account.administrators.order(:id).first
  app.content = [{ 'type' => 'frame', 'url' => panel_url }]
  app.save!
  puts "dashboard app #{title}: #{panel_url}"
end
puts 'bootstrap done'
