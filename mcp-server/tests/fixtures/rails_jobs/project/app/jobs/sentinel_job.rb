class SentinelJob < ApplicationJob
  after_enqueue { File.write("ATLAS_S26_SENTINEL_AFTER_ENQUEUE", "x") }
  before_perform { File.write("ATLAS_S26_SENTINEL_BEFORE_PERFORM", "x") }

  def perform(path)
    File.write(path, "x")
  end
end
