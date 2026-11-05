class StrictKwWorker
  include Sidekiq::Worker

  def perform(a, b:)
    [a, b]
  end
end
