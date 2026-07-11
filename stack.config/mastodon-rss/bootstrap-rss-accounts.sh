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
  application.scopes = "read write"
end
app.update!(scopes: "read write") unless app.scopes.to_s.split.sort == %w[read write]

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

observer_account = Account.find_or_initialize_by(username: "rss_observer", domain: nil)
observer_account.display_name = "RSS Timeline Observer"
observer_account.note = "Automated service account used to verify the RSS home timeline."
observer_account.discoverable = false
observer_account.save!

observer_user = User.find_or_initialize_by(email: "rss+observer@#{ENV.fetch("LOCAL_DOMAIN")}")
observer_user.account ||= observer_account
observer_user.password = SecureRandom.base64(48)
observer_user.password_confirmation = observer_user.password
observer_user.agreement = true if observer_user.respond_to?(:agreement=)
observer_user.accepted_rules = true if observer_user.respond_to?(:accepted_rules=)
observer_user.accepted_terms_at ||= Time.now.utc if observer_user.respond_to?(:accepted_terms_at=)
observer_user.approved = true if observer_user.respond_to?(:approved=)
observer_user.confirmed_at ||= Time.now.utc
observer_user.save!
observer_updates = {}
observer_updates[:approved] = true if observer_user.has_attribute?(:approved)
observer_updates[:disabled] = false if observer_user.has_attribute?(:disabled)
observer_user.update_columns(observer_updates) unless observer_updates.empty?

feeds.each do |feed|
  target_account = Account.find_by(username: feed.fetch("account").downcase, domain: nil)
  next if target_account.nil? || observer_account.following?(target_account)
  FollowService.new.call(observer_account, target_account, bypass_limit: true)
end

observer_token = Doorkeeper::AccessToken.where(application_id: app.id, resource_owner_id: observer_user.id, revoked_at: nil).where("scopes = ?", "read").order(id: :desc).first
observer_token ||= Doorkeeper::AccessToken.create!(application_id: app.id, resource_owner_id: observer_user.id, scopes: "read", expires_in: nil)

target = File.join(state_dir, "credentials.json")
temporary = "#{target}.tmp-#{Process.pid}"
File.write(temporary, JSON.generate(credentials))
File.chmod(0o600, temporary)
File.rename(temporary, target)
observer_target = File.join(state_dir, "observer.json")
observer_temporary = "#{observer_target}.tmp-#{Process.pid}"
File.write(observer_temporary, JSON.generate({ "username" => observer_account.username, "token" => observer_token.token, "following" => feeds.size }))
File.chmod(0o600, observer_temporary)
File.rename(observer_temporary, observer_target)
puts "[mastodon-rss] ensured #{credentials.size} local RSS accounts and observer follows #{feeds.size}"
RUBY
