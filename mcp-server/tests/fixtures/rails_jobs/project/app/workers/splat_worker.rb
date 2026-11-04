class SplatWorker
  include Sidekiq::Worker

  def perform(*args)
    args
  end
end
