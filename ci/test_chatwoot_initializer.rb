# Tests of charts/chatwoot/files/chart_initializer.rb: the dashboard session lifetime and
# the agent bot webhook to a private address.
#
# ruby ci/test_chatwoot_initializer.rb
require 'minitest/autorun'

INITIALIZER = File.expand_path('../charts/chatwoot/files/chart_initializer.rb', __dir__)

# the parts of Rails and devise_token_auth the initializer touches
class Integer
  def hours = self * 3600
end

module DeviseTokenAuth
  class << self
    attr_accessor :token_lifespan
  end
end

module Rails
  Config = Struct.new(:prepared) do
    # kept, so a test can run a block; Chatwoot's own classes are not loaded here
    def to_prepare(&block)
      self.prepared ||= []
      prepared << block
    end
  end
  Application = Struct.new(:config)

  def self.application
    @application ||= Application.new(Config.new)
  end
end

# Chatwoot's webhook sender, as far as the initializer patches it
module SafeFetch
  def self.allow_private_network? = false
end

module Webhooks
  class Trigger
    def initialize(url)
      @url = url
    end

    def execute = perform_request

    private

    def perform_request = SafeFetch.allow_private_network?
  end
end

class AgentBotWebhookTest < Minitest::Test
  BRIDGE = 'http://chatwoot-agent-bridge:8080/'

  def test_private_address_for_the_bridge_only
    env = ENV.to_h
    ENV['SMTP_ADDRESS'] = 'smtp.example.com'
    ENV['CHART_AGENT_BOT_URL'] = BRIDGE
    Rails.application.config.prepared = []
    load INITIALIZER
    Rails.application.config.prepared.first.call
    assert Webhooks::Trigger.new("#{BRIDGE}webhook").execute
    refute Webhooks::Trigger.new('http://10.0.0.1/webhook').execute
    refute Webhooks::Trigger.new('https://hooks.example.com/x').execute
    # the flag never outlives the request
    refute SafeFetch.allow_private_network?
  ensure
    ENV.replace(env)
  end
end

class OdooBridgeWebhookTest < Minitest::Test
  BRIDGE = 'http://chatwoot-odoo-bridge:8080/'

  def test_private_address_for_the_odoo_bridge_only
    env = ENV.to_h
    ENV['SMTP_ADDRESS'] = 'smtp.example.com'
    ENV.delete('CHART_AGENT_BOT_URL')
    ENV['CHART_ODOO_BRIDGE_URL'] = BRIDGE
    Rails.application.config.prepared = []
    load INITIALIZER
    Rails.application.config.prepared.first.call
    assert Webhooks::Trigger.new("#{BRIDGE}chatwoot/webhook").execute
    refute Webhooks::Trigger.new('http://10.0.0.1/webhook').execute
  ensure
    ENV.replace(env)
  end
end

class SessionHoursTest < Minitest::Test
  CHATWOOT_DEFAULT = 2 * 30 * 24 * 3600

  def setup
    @env = ENV.to_h
    ENV['SMTP_ADDRESS'] = 'smtp.example.com'
    ENV.delete('ACTIVE_STORAGE_SERVICE')
    ENV.delete('CHATWOOT_SESSION_HOURS')
    DeviseTokenAuth.token_lifespan = CHATWOOT_DEFAULT
  end

  def teardown
    ENV.replace(@env)
  end

  def run_initializer(hours)
    ENV['CHATWOOT_SESSION_HOURS'] = hours unless hours.nil?
    load INITIALIZER
  end

  def test_unset_keeps_chatwoot_default
    run_initializer(nil)
    assert_equal CHATWOOT_DEFAULT, DeviseTokenAuth.token_lifespan
  end

  def test_chart_default_of_36_hours
    run_initializer('36')
    assert_equal 36 * 3600, DeviseTokenAuth.token_lifespan
  end

  def test_bounds_are_accepted
    run_initializer('25')
    assert_equal 25 * 3600, DeviseTokenAuth.token_lifespan
    run_initializer('720')
    assert_equal 720 * 3600, DeviseTokenAuth.token_lifespan
  end

  # below 25 hours the dashboard session cookie expires at once and every login loops
  def test_short_or_malformed_values_stop_the_start
    ['12', '24', '0', '721', '', '36h', ' 36', '025', '-30', '1e2'].each do |hours|
      DeviseTokenAuth.token_lifespan = CHATWOOT_DEFAULT
      error = assert_raises(ArgumentError, "#{hours.inspect} accepted") { run_initializer(hours) }
      assert_match(/CHATWOOT_SESSION_HOURS must be a whole number from 25 to 720/, error.message)
      assert_equal CHATWOOT_DEFAULT, DeviseTokenAuth.token_lifespan, "#{hours.inspect} changed the lifespan"
    end
  end
end
