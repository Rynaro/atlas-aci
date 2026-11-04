class Enqueuer
  def run(user, report, ids, opts, job_class, blk, t)
    HardWorker.perform_async(user.id)
    HardWorker.perform_async(user.id, "fast")
    ReportJob.perform_later(report, :pdf)
    ::ReportJob.perform_later report, :csv
    PingWorker.perform_async
    PingWorker.perform_async()
    Billing::SyncJob.perform_later(user)
    ExportJob.perform_later(1)
    GhostJob.perform_later(1)
    NoPerformJob.perform_later(1)
    PlainTask.perform_async(1)
    HardWorker.perform_later(1)
    ReportJob.perform_async(1, 2)
    InheritedJob.perform_later(1, 2)
    SentinelWorker.perform_async("ATLAS_S26_SENTINEL_ASYNC")
    SentinelJob.perform_later("ATLAS_S26_SENTINEL_LATER")

    job_class.perform_later(1)
    self.class.perform_async(1)
    "ReportJob".constantize.perform_later(1, 2)
    perform_async(1)
    ReportJob.set(wait: 300).perform_later(report, :pdf)
    HardWorker.perform_in(300, user.id)
    HardWorker.perform_at(t, user.id)

    HardWorker.perform_async(*ids)
    ReportJob.perform_later(**opts)
    ReportJob.perform_later(report, format: :pdf)
    HardWorker.perform_async(user.id, &blk)
    HardWorker.perform_async(user.id) { |x| x }
    ReportJob.perform_later(1, 2, 3)
    HardWorker.perform_async
    SplatWorker.perform_async(1)
    OrderedWorker.perform_async(1, 2)
    StrictKwWorker.perform_async(1)
    ExportJob.perform_later(*ids)

    ReportJob.perform_now(report, :pdf)
    HardWorker.new.perform(1)
    HardWorker.perform_inline(1)
    HardWorker.perform_sync(1)
    HardWorker.delay.ping
    HardWorker.perform_bulk([[1]])
    # HardWorker.perform_async(1)
    "ReportJob.perform_later"
  end

  def fwd(...)
    HardWorker.perform_async(...)
  end
end
