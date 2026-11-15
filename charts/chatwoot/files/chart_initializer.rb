# Settings of the chart that Chatwoot has no environment variable for; mounted as
# config/initializers/zz_chart.rb, so it runs after Chatwoot's own initializers.

if ENV['ACTIVE_STORAGE_SERVICE'] == 's3_compatible'
  # the bucket endpoint is reachable only inside the cluster: Chatwoot streams the files
  # under its own host instead of redirecting the browser to the bucket
  Rails.application.config.active_storage.resolve_model_to_route = :rails_storage_proxy
  Rails.application.config.after_initialize do
    ActiveStorage.resolve_model_to_route = :rails_storage_proxy
  end
end

if ENV['SMTP_ADDRESS'].to_s.empty?
  # without a mail server nothing is sent; a failed delivery would retry in the worker
  ActiveSupport.on_load(:action_mailer) { self.perform_deliveries = false }
end

if ENV.key?('CHATWOOT_SESSION_HOURS')
  # a dashboard session ends after this many hours (Chatwoot sets two months); the next
  # one needs a login at the OIDC provider, which checks the group again. The dashboard
  # keeps its session cookie for whole days rounded down, so below 25 hours the cookie
  # expires at once and every login loops: refuse to start instead
  hours = ENV['CHATWOOT_SESSION_HOURS'].to_s
  unless hours.match?(/\A[1-9][0-9]*\z/) && (25..720).cover?(Integer(hours, 10))
    raise ArgumentError, "CHATWOOT_SESSION_HOURS must be a whole number from 25 to 720, got #{hours.inspect}"
  end
  DeviseTokenAuth.token_lifespan = Integer(hours, 10).hours
end

bridges = ENV.values_at('CHART_AGENT_BOT_URL', 'CHART_ODOO_BRIDGE_URL').map(&:to_s).reject(&:empty?)
unless bridges.empty?
  # Chatwoot refuses webhooks to private addresses (SafeFetch). The agent bot's bridge and
  # the Odoo bridge are Services of this release: allow a private address for a webhook to
  # their addresses only
  Rails.application.config.to_prepare do
    SafeFetch.singleton_class.prepend(Module.new do
      def allow_private_network?
        Thread.current[:chart_agent_bot_webhook] || super
      end
    end)
    Webhooks::Trigger.prepend(Module.new do
      private

      define_method(:perform_request) do
        return super() unless bridges.any? { |bridge| @url.to_s.start_with?(bridge) }

        Thread.current[:chart_agent_bot_webhook] = true
        begin
          super()
        ensure
          Thread.current[:chart_agent_bot_webhook] = nil
        end
      end
    end)
  end
end

# The Platform API never hands out a super admin: no login link, no API token, no change
# and no adoption of an existing super admin by a Platform App. The login bridge uses this
# API; a super admin logs in with its password at /super_admin only.
Rails.application.config.to_prepare do
  Platform::Api::V1::UsersController.class_eval do
    before_action :chart_refuse_super_admin, only: %i[login token update destroy]
    before_action :chart_refuse_super_admin_email, only: %i[create]

    private

    def chart_refuse_super_admin
      render json: { error: 'super admin' }, status: :forbidden if @resource.is_a?(SuperAdmin)
    end

    def chart_refuse_super_admin_email
      user = User.from_email(params[:email].to_s)
      render json: { error: 'super admin' }, status: :forbidden if user.is_a?(SuperAdmin)
    end
  end
end
