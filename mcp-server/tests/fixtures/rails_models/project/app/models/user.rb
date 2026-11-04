class User < ApplicationRecord
  has_many :posts, class_name: "Blog::Post", dependent: :destroy
  has_one :profile, class_name: "::Profile", inverse_of: :user
  has_many :invoices, class_name: "Invoice"
  belongs_to :team, class_name: "Team"
  belongs_to :widget, class_name: "Widget"
  belongs_to :ghost, class_name: "Ghost"
  belongs_to :vault, class_name: "Vault"
  has_many :comments

  with_options(dependent: :destroy) do
    has_many :drafts, class_name: "Blog::Post"
  end

  validates :name, presence: true
  after_save { File.write("ATLAS_S25_SENTINEL_CALLBACK", "x") }
end
