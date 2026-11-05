class PingWorker
  include ::Sidekiq::Worker

  def perform
    :pong
  end
end
