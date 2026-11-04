module Blog
  class Post < ApplicationRecord
    PREFIX = "x"
    SHARED = [].freeze

    self.table_name = :blog_posts

    belongs_to :author, class_name: "User"
    has_many :tags, class_name: "Tag"
    has_many :global_tags, class_name: "::Tag"
    has_many :labels, class_name: "Label"

    has_many :taggings, through: :post_tags, class_name: "Tagging"
    belongs_to :commentable, polymorphic: true
    has_many :readers, class_name: "#{PREFIX}User"
    has_many :editors, class_name: User.name
    has_many :owners, class_name: :User
    has_many :partners, class_name: "user"
    has_many :recent, -> { order(:id) }, class_name: "Comment"
    has_many :versions, class_name: "Version" do
    end
    has_many(*SHARED)
    has_many relation_name, class_name: "User"
    has_and_belongs_to_many :categories
    has_many :tenants, as: :tenant
    has_many :loners, anonymous_class: Object
    has_many :y, :z, class_name: "User"
  end
end
