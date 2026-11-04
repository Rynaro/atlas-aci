class CreateUsers < ActiveRecord::Migration[7.1]
  def change
    create_table :users do |t|
      t.string :name
    end
    create_table "teams", force: true
    add_column :users, :email, :string, null: false
    remove_column :users, :legacy_flag, :boolean
  end
end
