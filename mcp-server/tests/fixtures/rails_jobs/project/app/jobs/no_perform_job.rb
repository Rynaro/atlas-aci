class NoPerformJob < ApplicationJob
  def self.perform(x)
    x
  end

  class << self
    def perform(y)
      y
    end
  end
end
