class LegacyRows < ActiveRecord::Migration
  def self.up
    create_table :old_style
  end

  def change
    create_table :legacy_rows
  end
end
