class HardWorker
  include Sidekiq::Job

  def perform(user_id, mode = "full", notify: false, &blk)
    [user_id, mode, notify, blk]
  end

  def ping
    :pong
  end
end
