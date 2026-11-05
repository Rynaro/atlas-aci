class VendoredWorker
  include Sidekiq::Job

  def perform(x)
    x
  end
end

VendoredWorker.perform_async(1)
HardWorker.perform_async(1)
