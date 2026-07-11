#!/usr/bin/env bash
set -euo pipefail

feed_dir="${MASTODON_RSS_FEED_DIR:?missing MASTODON_RSS_FEED_DIR}"
state_dir="${MASTODON_RSS_STATE_DIR:?missing MASTODON_RSS_STATE_DIR}"
mkdir -p "$state_dir"

MASTODON_RSS_FEED_DIR="$feed_dir" MASTODON_RSS_STATE_DIR="$state_dir" bundle exec rails runner - <<'RUBY'
require "json"
require "securerandom"

feed_dir = ENV.fetch("MASTODON_RSS_FEED_DIR")
state_dir = ENV.fetch("MASTODON_RSS_STATE_DIR")
feeds = Dir.glob(File.join(feed_dir, "*.json")).sort.flat_map do |path|
  JSON.parse(File.read(path)).fetch("feeds")
end
credentials = {}
app = Doorkeeper::Application.find_or_create_by!(name: "mastodon-rss-publisher") do |application|
  application.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
  application.scopes = "write"
end

feeds.each do |feed|
  username = feed.fetch("account").downcase
  account = Account.find_or_initialize_by(username: username, domain: nil)
  account.display_name = feed.fetch("display_name")
  account.note = "Automated RSS feed for #{feed.fetch("source")}. Links point to the original publisher."
  account.discoverable = true
  account.save!

  user = User.find_or_initialize_by(email: "rss+#{username}@#{ENV.fetch("LOCAL_DOMAIN")}")
  user.account ||= account
  user.password = SecureRandom.base64(48)
  user.password_confirmation = user.password
  user.agreement = true if user.respond_to?(:agreement=)
  user.accepted_rules = true if user.respond_to?(:accepted_rules=)
  user.accepted_terms_at ||= Time.now.utc if user.respond_to?(:accepted_terms_at=)
  user.approved = true if user.respond_to?(:approved=)
  user.confirmed_at ||= Time.now.utc
  user.save!
  updates = {}
  updates[:approved] = true if user.has_attribute?(:approved)
  updates[:disabled] = false if user.has_attribute?(:disabled)
  user.update_columns(updates) unless updates.empty?

  token = Doorkeeper::AccessToken.where(application_id: app.id, resource_owner_id: user.id, revoked_at: nil).order(id: :desc).first
  token ||= Doorkeeper::AccessToken.create!(application_id: app.id, resource_owner_id: user.id, scopes: "write", expires_in: nil)
  credentials[username] = { "token" => token.token }
end

target = File.join(state_dir, "credentials.json")
temporary = "#{target}.tmp-#{Process.pid}"
File.write(temporary, JSON.generate(credentials))
File.chmod(0o600, temporary)
File.rename(temporary, target)
puts "[mastodon-rss] ensured #{credentials.size} local RSS accounts"
RUBY
