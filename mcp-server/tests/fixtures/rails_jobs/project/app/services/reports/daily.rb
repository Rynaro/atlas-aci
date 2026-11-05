class Reports::Daily
  def go
    ReportJob.perform_later(1, 2)
  end
end
