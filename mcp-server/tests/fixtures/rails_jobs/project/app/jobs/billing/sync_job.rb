module Billing
  class SyncJob < ApplicationJob
    def perform(account)
      account
    end
  end
end
