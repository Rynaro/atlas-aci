class SentinelWorker
  include Sidekiq::Job

  File.write("ATLAS_S26_SENTINEL_CLASS_BODY", "x")

  def perform(path)
    File.write(path, "x")
  end
end
