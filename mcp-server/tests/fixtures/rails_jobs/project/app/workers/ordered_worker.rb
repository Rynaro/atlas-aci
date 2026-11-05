class OrderedWorker
  include Sidekiq::Worker

  def perform(a, b = 1, c)
    [a, b, c]
  end
end
