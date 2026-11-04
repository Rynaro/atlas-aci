class Archive < ApplicationRecord
  SUFFIX = "x"
  self.table_name = "arch_#{SUFFIX}"

  def self.table_name
    "archive_rows"
  end
end
