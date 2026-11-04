class SyncJob < ::ActiveJob::Base
  def perform(account, mode)
    [account, mode]
  end
end
