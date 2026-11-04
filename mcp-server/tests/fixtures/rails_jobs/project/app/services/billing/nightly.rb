module Billing
  class Nightly
    def run
      SyncJob.perform_later(1)
      ReportJob.perform_later(1, 2)
    end
  end
end
