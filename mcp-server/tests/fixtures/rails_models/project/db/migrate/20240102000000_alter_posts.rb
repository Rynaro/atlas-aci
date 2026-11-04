class AlterPosts < ActiveRecord::Migration[7.1]
  def up
    add_column "blog_posts", "title", :string
    change_table :posts do |t|
      t.string :slug
    end
    drop_table :old_posts
    rename_table :posts, :articles
    rename_column :users, :name, :full_name
    add_reference :posts, :user
    add_index :users, :email
    execute "DELETE FROM users"
    reversible do |dir|
      dir.up { }
    end
    add_index :users, :name if ENV["ATLAS"]
    add_column table_name, :a, :string
    add_column :users, column_name, :string
    create_table "logs_#{year}"
    remove_column :users
    File.write("ATLAS_S25_SENTINEL_MIGRATION", "x")
  end

  def down
    remove_column :blog_posts, :title
  end
end
