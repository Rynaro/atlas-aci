module Taggable
  extend ActiveSupport::Concern

  included do
    has_many :tags, class_name: "Tag"
  end

  class Helper < ApplicationRecord
    has_many :users, class_name: "User"
  end
end
